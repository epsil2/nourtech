"""Hermes executions (control plane, module C).

Camilia owns the record, the policy and the traceability; Hermes owns the run.
The adapter refuses to start anything when the engine is missing, and every state
transition that Camilia reports comes from a real ``hermes kanban`` read — never
from an optimistic local guess.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import cp_jobs
from .hermes_kanban import HermesError, HermesUnavailable, KanbanAdapter, map_status
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
    context: str | None = Field(default=None, max_length=8000)
    max_runtime: str = Field(default="30m", max_length=10)
    max_retries: int = Field(default=1, ge=0, le=5)
    dispatch: bool = False


class CancelIn(BaseModel):
    reason: str = Field(default="arrêt demandé depuis Camilia", max_length=300)


@router.get("/api/v1/executions")
def list_executions(request: Request, project_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    rows = _store(request).cp_list("execution", project_id=project_id, limit=min(max(limit, 1), 200))
    return {"executions": rows, "total": len(rows)}


@router.get("/api/v1/executions/engine")
def engine_status() -> dict[str, Any]:
    """What the API can actually do with Hermes on this host."""
    adapter = _adapter()
    return adapter.status()


@router.post("/api/v1/executions", status_code=201)
def create_execution(payload: ExecutionIn, request: Request) -> dict[str, Any]:
    store = _store(request)
    project = store.cp_get("project", payload.project_id)
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
            "jira_key": payload.jira_key,
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
    events = [
        doc
        for doc in store.cp_list("execution_event", where={"execution_id": execution_id}, limit=200)
    ]
    events.sort(key=lambda d: (d.get("created_at") or "", d.get("seq") or 0))
    return {"execution": execution, "events": events}


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
