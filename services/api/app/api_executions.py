"""Hermes executions (control plane, module C).

Camilia owns the record, the policy and the traceability; Hermes owns the run.
The adapter refuses to start anything when the engine is missing, and every state
transition that Camilia reports comes from a real ``hermes kanban`` read — never
from an optimistic local guess.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import cp_jobs
from .hermes_kanban import HermesError, HermesUnavailable, KanbanAdapter, map_status
from .jira import JiraClient, JiraError, JiraNotConfigured
from . import security
from .redaction import redact

router = APIRouter(tags=["executions"])

TERMINAL = {"succeeded", "failed", "cancelled", "archived", "blocked"}


def _store(request: Request):
    return request.app.state.store


def _actor(request: Request) -> str:
    # verified identity only: the session cookie, never a client-supplied header
    return security.actor_of(request)[:80]


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _adapter() -> KanbanAdapter:
    return KanbanAdapter()


def _get_or_404(request: Request, execution_id: str) -> dict[str, Any]:
    doc = _store(request).cp_get("execution", execution_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="execution not found")
    return doc


ISSUE_KEY = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")


def _jira() -> JiraClient | None:
    client = JiraClient()
    return client if client.configured else None


def _keys_in_scope(
    *, sprint: str | None = None, feature: str | None = None, story: str | None = None
) -> tuple[set[str] | None, str | None]:
    """Jira issue keys matching the selection, or (None, warning) when unknown.

    `None` means "do not narrow": the caller keeps every execution rather than
    silently showing an empty list because Jira was unreachable.
    """
    clauses: list[str] = []
    for value, field in ((story, "key"), (feature, "parent")):
        if value:
            if not ISSUE_KEY.match(value.strip().upper()):
                return None, f"clé Jira ignorée (format invalide) : {value}"
            clauses.append(f"{field} = {value.strip().upper()}")
    if sprint:
        if not sprint.strip().isdigit():
            return None, f"sprint ignoré (identifiant non numérique) : {sprint}"
        clauses.append(f"sprint = {sprint.strip()}")

    if not clauses:
        return None, None

    client = _jira()
    if client is None:
        return None, "Jira non configuré : filtre sprint/feature/user story ignoré"
    try:
        issues = client.board_issues(" AND ".join(clauses) + " ORDER BY key ASC", limit=500)
    except (JiraError, JiraNotConfigured) as exc:
        return None, f"Jira indisponible ({exc}) : filtre ignoré"
    return {str(issue.get("key") or "").upper() for issue in issues if issue.get("key")}, None


def _advance_jira(store, execution: dict[str, Any], targets: list[str], reason: str) -> dict[str, Any]:
    """Move the execution's ticket through the write gate, then re-read its status.

    The move goes through JiraWriter, which refuses unless JIRA_WRITE_ENABLED is
    set and the project is allow-listed — so an execution can never move a ticket
    the operator did not authorise. Whatever happens, the live status is read back
    and stored: the executions list shows Jira's truth, not our intention.
    """
    from .jira_write import JiraWriter, JiraWriteDisabled, JiraWriteForbidden

    key = str(execution.get("jira_key") or "").strip().upper()
    if not key:
        return {}
    client = _jira()
    if client is None:
        return {}

    result: dict[str, Any] = {"moved": False, "reason": "aucune cible demandée"}
    if targets:
        writer = JiraWriter()
        if not writer.enabled:
            result = {"moved": False, "reason": "écriture Jira désactivée (JIRA_WRITE_ENABLED)"}
        else:
            for target in targets:
                try:
                    moved = writer.transition(key, target)
                except JiraWriteDisabled as exc:
                    result = {"moved": False, "reason": str(exc)}
                    break
                except JiraWriteForbidden as exc:
                    result = {"moved": False, "reason": str(exc)}
                    break
                except Exception as exc:  # noqa: BLE001 - a Jira hiccup must not fail the sync
                    result = {"moved": False, "reason": str(exc)}
                    continue
                if moved.get("moved", True):
                    result = {**moved, "moved": True, "to": moved.get("to") or target}
                    break
                result = moved

    status = None
    try:
        status = client.issue_status(key).get("status")
    except (JiraError, JiraNotConfigured):
        status = None

    _event(
        store,
        execution,
        "jira",
        f"{key} · {reason} · "
        + (f"→ {result.get('to')}" if result.get("moved") else str(result.get("reason") or "inchangé"))
        + (f" · statut Jira : {status}" if status else ""),
        {"targets": targets, **result},
    )
    fields: dict[str, Any] = {"jira_status": status, "jira_synced_at": _now()}
    if result.get("moved"):
        fields["jira_transitioned_to"] = result.get("to")
    return store.cp_patch("execution", execution["id"], fields) or {}


def _event(store, execution: dict[str, Any], kind: str, message: str, payload: dict[str, Any] | None = None) -> None:
    store.cp_save(
        "execution_event",
        {
            "project_id": execution.get("project_id"),
            "execution_id": execution["id"],
            "kind": kind,
            "message": message[:400],
            "payload": redact(payload or {}),
        },
        project_id=execution.get("project_id"),
    )


def _task_body(execution: dict[str, Any]) -> str:
    lines = [
        f"Projet Camilia : {execution.get('project_key') or execution.get('project_id')}",
        f"Objectif : {execution.get('objective') or ''}",
    ]
    if execution.get("jira_key"):
        lines.append(f"Work item Jira : {execution['jira_key']}")
    if execution.get("agent_id"):
        lines.append(f"Agent : {execution['agent_id']}")
    if execution.get("context"):
        lines.append("Contexte :")
        lines.append(str(execution["context"])[:6000])
    lines += [
        "",
        "Contraintes d'exécution :",
        "- travailler dans le worktree fourni par le board, ne rien pousser sur main ;",
        "- si un changement de code est nécessaire, ouvrir une branche dédiée et une pull request ;",
        "- produire comme preuve : fichiers modifiés, commandes exécutées, tests lancés, résultat ;",
        "- si une étape échoue, s'arrêter et décrire le blocage au lieu de simuler un succès.",
        f"Identifiant d'exécution Camilia : {execution['id']}",
    ]
    return "\n".join(lines)


class ExecutionIn(BaseModel):
    project_id: str
    objective: str = Field(min_length=5, max_length=2000)
    agent_id: str | None = None
    jira_key: str | None = None
    sprint_id: str | None = Field(default=None, max_length=40)
    sprint_name: str | None = Field(default=None, max_length=120)
    feature_key: str | None = Field(default=None, max_length=40)
    story_key: str | None = Field(default=None, max_length=40)
    context: str | None = Field(default=None, max_length=8000)
    max_runtime: str = Field(default="30m", max_length=10)
    max_retries: int = Field(default=1, ge=0, le=5)
    dispatch: bool = False


class CancelIn(BaseModel):
    reason: str = Field(default="arrêt demandé depuis Camilia", max_length=300)


@router.get("/api/v1/executions")
def list_executions(
    request: Request,
    project_id: str | None = None,
    sprint: str | None = None,
    feature: str | None = None,
    story: str | None = None,
    q: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Executions, narrowed by the same four filters as the backlog.

    Sprint / feature / user story are Jira notions: we resolve them to a set of
    issue keys once, then keep the executions whose ticket belongs to that set.
    Resolving a filter never fails the request — an unreachable Jira simply
    leaves the list unfiltered and says so in `warnings`.
    """
    rows = _store(request).cp_list("execution", project_id=project_id, limit=min(max(limit, 1), 200))
    warnings: list[str] = []

    keys, warning = _keys_in_scope(sprint=sprint, feature=feature, story=story)
    if warning:
        warnings.append(warning)
    if keys is not None:
        rows = [row for row in rows if str(row.get("jira_key") or "").upper() in keys]
    if sprint and keys is None and sprint.isdigit():
        rows = [row for row in rows if str(row.get("sprint_id") or "") == sprint]
    if q and q.strip():
        needle = q.strip().lower()
        rows = [
            row
            for row in rows
            if needle in str(row.get("objective") or "").lower()
            or needle in str(row.get("jira_key") or "").lower()
            or needle in str(row.get("agent_id") or "").lower()
        ]
    return {"executions": rows, "total": len(rows), "warnings": warnings}


@router.get("/api/v1/executions/engine")
def engine_status() -> dict[str, Any]:
    """What the API can actually do with Hermes on this host."""
    adapter = _adapter()
    return adapter.status()


@router.post("/api/v1/executions", status_code=201)
def create_execution(payload: ExecutionIn, request: Request) -> dict[str, Any]:
    store = _store(request)
    project = store.cp_get("project", payload.project_id)
    if project is None and payload.project_id.startswith("jira:"):
        # A Jira project picked in the dropdown has no Camilia row yet: create it
        # on first use so executions can be launched straight from the backlog.
        key = payload.project_id.split(":", 1)[1].strip().upper()
        if not ISSUE_KEY.match(f"{key}-1"):
            raise HTTPException(status_code=422, detail="invalid Jira project key")
        project = store.cp_save(
            "project",
            {
                "key": key,
                "name": key,
                "jira_project": key,
                "status": "active",
                "stage": "delivery",
                "business_request": "Importé depuis Jira au lancement d'une exécution",
            },
        )
        store.add_audit(_actor(request), "project.imported_from_jira", "project", project["id"], {"key": key})
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    adapter = _adapter()
    if not adapter.available:
        raise HTTPException(
            status_code=409,
            detail="Hermes is not available on this host; no execution was created",
        )

    idem = (request.headers.get("idempotency-key") or cp_jobs.new_key("cp-"))[:80]
    existing = store.cp_list("execution", where={"execution_key": idem}, limit=1)
    if existing:
        return existing[0]

    board = f"camilia-{(project.get('key') or 'proj').lower()}"[:48]
    execution = store.cp_save(
        "execution",
        {
            "project_id": payload.project_id,
            "project_key": project.get("key"),
            "agent_id": payload.agent_id,
            "jira_key": (payload.jira_key or "").strip().upper() or None,
            "sprint_id": payload.sprint_id,
            "sprint_name": payload.sprint_name,
            "feature_key": (payload.feature_key or "").strip().upper() or None,
            "story_key": (payload.story_key or "").strip().upper() or None,
            "objective": payload.objective[:2000],
            "context": (payload.context or "")[:8000],
            "board": board,
            "execution_key": idem,
            "status": "starting",
            "task_id": None,
            "engine": "hermes-kanban",
            "max_runtime": payload.max_runtime,
            "max_retries": payload.max_retries,
        },
        project_id=payload.project_id,
    )
    try:
        adapter.ensure_board(board, name=f"Camilia {project.get('key')}")
        task = adapter.create_task(
            board=board,
            title=f"{project.get('key')} · {payload.objective[:120]}",
            body=_task_body(execution),
            idempotency_key=idem,
            max_runtime=payload.max_runtime,
            max_retries=payload.max_retries,
        )
    except (HermesUnavailable, HermesError, ValueError) as exc:
        store.cp_patch("execution", execution["id"], {"status": "failed", "error": str(exc)[:500]})
        _event(store, execution, "error", f"création de tâche impossible : {exc}")
        raise HTTPException(status_code=502, detail=f"hermes rejected the task: {exc}") from exc

    execution = store.cp_patch(
        "execution",
        execution["id"],
        {
            "task_id": task.get("id"),
            "status": map_status(task.get("status")),
            "kanban_status": task.get("status"),
            "board_ref": task.get("board") or board,
        },
    ) or execution
    _event(store, execution, "created", f"tâche kanban {task.get('id')} créée ({task.get('status')})")
    if execution.get("jira_key"):
        execution = _advance_jira(store, execution, ["In Progress", "In progress", "En cours"], "lancement") or execution
    store.add_audit(
        _actor(request), "execution.created", "execution", execution["id"],
        redact({"task_id": task.get("id"), "board": board}),
    )

    if payload.dispatch:
        job = cp_jobs.submit(
            store,
            kind="execution.dispatch",
            subject_type="execution",
            subject_id=execution["id"],
            project_id=payload.project_id,
            actor=_actor(request),
            fn=lambda: adapter.dispatch(board, 1),
        )
        execution = store.cp_patch("execution", execution["id"], {"dispatch_job": job["id"]}) or execution
    return execution


@router.get("/api/v1/executions/{execution_id}")
def get_execution(execution_id: str, request: Request) -> dict[str, Any]:
    store = _store(request)
    execution = _get_or_404(request, execution_id)
    # The ticket's live status is what the delivery team reads: refresh it on
    # open, but never let an unreachable Jira hide the execution itself.
    jira = None
    if execution.get("jira_key"):
        client = _jira()
        if client is not None:
            try:
                jira = client.issue_status(execution["jira_key"])
            except (JiraError, JiraNotConfigured) as exc:
                jira = {"key": execution["jira_key"], "status": None, "error": str(exc)}
            else:
                store.cp_patch(
                    "execution",
                    execution_id,
                    {"jira_status": jira.get("status"), "jira_synced_at": _now()},
                )
    events = [
        doc
        for doc in store.cp_list("execution_event", where={"execution_id": execution_id}, limit=200)
    ]
    events.sort(key=lambda d: (d.get("created_at") or "", d.get("seq") or 0))
    return {"execution": execution, "events": events, "jira": jira}


@router.post("/api/v1/executions/{execution_id}/sync")
def sync_execution(execution_id: str, request: Request) -> dict[str, Any]:
    store = _store(request)
    execution = _get_or_404(request, execution_id)
    task_id = execution.get("task_id")
    if not task_id:
        raise HTTPException(status_code=409, detail="this execution has no Hermes task")

    adapter = _adapter()
    try:
        task = adapter.show(execution.get("board") or "", task_id)
        runs = adapter.runs(execution.get("board") or "", task_id)
    except (HermesUnavailable, HermesError, ValueError) as exc:
        _event(store, execution, "error", f"lecture impossible : {exc}")
        raise HTTPException(status_code=502, detail=f"cannot read the Hermes task: {exc}") from exc

    kanban_status = task.get("status")
    status = map_status(kanban_status)
    # A cancelled execution stays cancelled: Hermes reports the block we asked for,
    # it must not overwrite the decision Camilia recorded.
    if execution.get("status") == "cancelled" and status == "blocked":
        status = "cancelled"
    fields: dict[str, Any] = {
        "kanban_status": kanban_status,
        "last_synced_at": _now(),
        "runs": int(task.get("run_count") or (len(runs) if isinstance(runs, list) else 0)),
    }
    if task.get("completion_contract"):
        fields["completion_contract"] = task["completion_contract"]
    for key in ("started_at", "finished_at", "exit_code", "tokens", "cost", "error"):
        if task.get(key) is not None:
            fields[key] = task[key]
    if status != execution.get("status"):
        fields["status"] = status

    updated = store.cp_patch("execution", execution_id, fields) or execution
    if status != execution.get("status"):
        _event(store, updated, "status", f"{execution.get('status')} → {status}")
        if status in TERMINAL:
            store.add_audit("hermes", f"execution.{status}", "execution", execution_id, redact({"runs": len(runs or [])}))
        if updated.get("jira_key"):
            targets: list[str] = []
            if status == "running":
                targets = ["In Progress", "In progress", "En cours"]
            elif status == "succeeded":
                targets = ["Done", "Terminé", "Terminé(e)", "Closed", "Fermé"]
            else:
                targets = []  # refresh the Jira status without moving the ticket
            updated = _advance_jira(store, updated, targets, f"statut {status}") or updated
    latest = runs[-1] if isinstance(runs, list) and runs else None
    if isinstance(latest, dict):
        summary = redact(str(latest.get("summary") or latest.get("outcome") or "sans résumé"))[:200]
        _event(
            store,
            updated,
            "run",
            f"run {latest.get('id', '?')} · {latest.get('status', '?')} · {summary}",
            {"run": latest.get("id"), "status": latest.get("status"), "summary": summary},
        )
    return updated


@router.post("/api/v1/executions/{execution_id}/dispatch", status_code=202)
def dispatch_execution(execution_id: str, request: Request) -> dict[str, Any]:
    store = _store(request)
    execution = _get_or_404(request, execution_id)
    board = execution.get("board") or ""
    if not board:
        raise HTTPException(status_code=409, detail="execution has no board")
    adapter = _adapter()
    if not adapter.available:
        raise HTTPException(status_code=409, detail="Hermes is not available on this host")
    job = cp_jobs.submit(
        store,
        kind="execution.dispatch",
        subject_type="execution",
        subject_id=execution_id,
        project_id=execution.get("project_id"),
        actor=_actor(request),
        fn=lambda: adapter.dispatch(board, 1),
    )
    _event(store, execution, "dispatch", "dispatch demandé")
    return {"job": job, "poll": f"/api/v1/jobs/{job['id']}"}


@router.post("/api/v1/executions/{execution_id}/cancel", status_code=202)
def cancel_execution(execution_id: str, payload: CancelIn, request: Request) -> dict[str, Any]:
    """Block the Hermes task; the kanban CLI has no cancel verb, so blocking is the
    documented way to stop a task from being picked up, and the reason is preserved."""
    store = _store(request)
    execution = _get_or_404(request, execution_id)
    if execution.get("status") in TERMINAL:
        raise HTTPException(status_code=409, detail=f"execution is already {execution['status']}")
    task_id, board = execution.get("task_id"), execution.get("board")
    if not task_id or not board:
        raise HTTPException(status_code=409, detail="execution has no Hermes task to cancel")
    adapter = _adapter()
    try:
        adapter.block(board, task_id, payload.reason)
    except (HermesUnavailable, HermesError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"hermes refused the cancellation: {exc}") from exc
    updated = store.cp_patch(
        "execution", execution_id, {"status": "cancelled", "cancel_reason": payload.reason[:300], "cancelled_at": _now()}
    ) or execution
    _event(store, updated, "cancelled", payload.reason[:300])
    store.add_audit(_actor(request), "execution.cancelled", "execution", execution_id, {"reason": payload.reason[:300]})
    return updated


def boot(store) -> None:
    for job in store.cp_list("execution", status="starting", limit=100):
        store.cp_patch(
            "execution",
            job["id"],
            {"status": "failed", "error": "api restarted before the Hermes task was recorded"},
        )
