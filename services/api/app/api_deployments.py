"""Environments, deployments and control-plane metrics (modules F/G, minus governance).

Deployment facts on this host: the release chain is
``DEV -> QA -> human tag -> factory-release-watcher (VPS) -> PROD``.  Camilia does
not re-implement it.  It records the promotion request, enforces the human gate on
PRE-PROD and PROD, and stores the outcome that the operator or the watcher reports.
A deployment is never marked ``deployed`` from a plan: only from a confirmation
carrying evidence.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from . import security
from .jira import JiraClient, JiraError, JiraNotConfigured
from .redaction import redact

router = APIRouter(tags=["deployments"])

ENVIRONMENTS = ("DEV", "QA", "PRE-PROD", "PROD")
GATED = {"PRE-PROD", "PROD"}


def _store(request: Request):
    return request.app.state.store


def _actor(request: Request) -> str:
    # verified identity only: the session cookie, never a client-supplied header
    return security.actor_of(request)[:80]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class DeploymentIn(BaseModel):
    project_id: str = Field(min_length=1, max_length=80)
    environment: str = Field(max_length=10)
    version: str = Field(min_length=1, max_length=80)
    commit_sha: str = Field(default="", max_length=40)
    release_id: str | None = None
    sprint_id: str = Field(default="", max_length=32)
    sprint_name: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=400)

    @field_validator("commit_sha")
    @classmethod
    def _commit_is_a_sha(cls, value: str) -> str:
        """Empty means "whatever the pipeline publishes"; anything else is a sha."""
        commit = (value or "").strip().lower()
        if commit and not re.fullmatch(r"[0-9a-f]{7,40}", commit):
            raise ValueError("commit_sha must be 7 to 40 hexadecimal characters, or empty")
        return commit


class ConfirmIn(BaseModel):
    outcome: str = Field(default="deployed", max_length=20)
    actor: str | None = None
    evidence: str = Field(default="", max_length=1000)
    go: bool = True


class RollbackIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


@router.get("/api/v1/environments")
def list_environments(request: Request) -> dict[str, Any]:
    rows = _store(request).cp_list("environment", limit=20)
    order = {name: index for index, name in enumerate(ENVIRONMENTS)}
    rows.sort(key=lambda row: order.get(row.get("name"), 99))
    return {"environments": rows}


@router.get("/api/v1/deployments")
def list_deployments(request: Request, project_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    rows = _store(request).cp_list("deployment", project_id=project_id, limit=min(max(limit, 1), 200))
    return {"deployments": rows, "total": len(rows)}


@router.get("/api/v1/deployments/projects")
def deployment_projects(request: Request) -> dict[str, Any]:
    """Every project a deployment may target.

    Camilia's own project rows are the ones a deployment is stored against, but
    the control plane starts empty while Jira already knows the real portfolio.
    An empty drop-down is a bug the operator cannot fix from the page, so the
    Jira projects are listed too — as ``jira:KEY`` — and materialised as a
    Camilia project the first time a deployment targets them.
    """
    store = _store(request)
    rows = store.cp_list("project", limit=200)
    items: list[dict[str, Any]] = []
    known: set[str] = set()
    for row in rows:
        key = str(row.get("jira_project") or row.get("key") or "").upper()
        known.add(key)
        items.append(
            {
                "id": row["id"],
                "key": row.get("key"),
                "name": row.get("name"),
                "jira_project": row.get("jira_project") or key or None,
                "source": "camilia",
                "board": None,
            }
        )

    jira_available = False
    try:
        client = JiraClient()
        boards: dict[str, int] = {}
        for board in client.boards():
            project_key = str(board.get("project_key") or "").upper()
            if project_key and project_key not in boards:
                boards[project_key] = int(board["id"])
        for project in client.projects():
            jira_available = True
            key = str(project.get("key") or "").upper()
            if not key or key in known:
                continue
            items.append(
                {
                    "id": f"jira:{key}",
                    "key": key,
                    "name": project.get("name") or key,
                    "jira_project": key,
                    "source": "jira",
                    "board": boards.get(key),
                }
            )
    except (JiraNotConfigured, JiraError, AttributeError, TypeError, ValueError):
        # Jira down or unconfigured must not empty a page the operator needs
        jira_available = False

    items.sort(key=lambda row: (str(row.get("source")), str(row.get("key") or "")))
    return {"projects": items, "count": len(items), "jira": jira_available}


def _resolve_project(store: Any, identifier: str, request: Request) -> dict[str, Any]:
    """Accept a Camilia project id or a ``jira:KEY`` handle."""
    if identifier.startswith("jira:"):
        key = identifier.split(":", 1)[1].strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9]{1,19}", key):
            raise HTTPException(status_code=422, detail="jira project key is not a Jira key")
        existing = [p for p in store.cp_list("project", limit=200) if str(p.get("jira_project") or "").upper() == key]
        if existing:
            return existing[0]
        name = key
        try:
            found = [p for p in JiraClient().projects() if str(p.get("key") or "").upper() == key]
            if found:
                name = str(found[0].get("name") or key)
        except (JiraNotConfigured, JiraError):
            pass  # the key is enough to store the request; the name is cosmetic
        project = store.cp_save(
            "project",
            {
                "key": key,
                "name": name[:120],
                "jira_project": key,
                "status": "draft",
                "stage": "deployment",
                "source": "jira",
            },
        )
        store.add_audit(_actor(request), "project.imported_from_jira", "project", project["id"], {"key": key})
        return project

    project = store.cp_get("project", identifier)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


@router.post("/api/v1/deployments", status_code=201)
def request_deployment(payload: DeploymentIn, request: Request) -> dict[str, Any]:
    store = _store(request)
    project = _resolve_project(store, payload.project_id, request)
    environment = payload.environment.strip().upper()
    if environment not in ENVIRONMENTS:
        raise HTTPException(status_code=422, detail=f"environment must be one of {', '.join(ENVIRONMENTS)}")

    deployment = store.cp_save(
        "deployment",
        {
            "project_id": project["id"],
            "project_key": project.get("key"),
            "environment": environment,
            "version": payload.version[:80],
            "commit_sha": payload.commit_sha[:40],
            "release_id": payload.release_id,
            "sprint_id": payload.sprint_id[:32],
            "sprint_name": payload.sprint_name[:120],
            "note": payload.note[:400],
            "status": "awaiting_approval" if environment in GATED else "requested",
            "requested_by": _actor(request),
            "requested_at": _now(),
        },
        project_id=project["id"],
    )
    store.add_audit(
        _actor(request),
        "deployment.requested",
        "deployment",
        deployment["id"],
        redact(
            {
                "environment": environment,
                "version": payload.version[:80],
                "sprint": payload.sprint_name[:120],
            }
        ),
    )
    return deployment


@router.post("/api/v1/deployments/{deployment_id}/confirm")
def confirm_deployment(deployment_id: str, payload: ConfirmIn, request: Request) -> dict[str, Any]:
    store = _store(request)
    deployment = store.cp_get("deployment", deployment_id)
    if deployment is None:
        raise HTTPException(status_code=404, detail="deployment not found")
    if deployment["status"] in {"deployed", "rejected", "rolled_back"}:
        raise HTTPException(status_code=409, detail=f"deployment is already {deployment['status']}")

    actor = (payload.actor or _actor(request)).strip()[:80]
    if actor in {"", "anonymous"}:
        raise HTTPException(status_code=422, detail="an explicit operator is required")
    environment = deployment.get("environment")
    if environment in GATED and not payload.go:
        store.cp_patch("deployment", deployment_id, {"status": "rejected", "decided_by": actor, "decided_at": _now()})
        store.add_audit(actor, "deployment.rejected", "deployment", deployment_id, {"environment": environment})
        return store.cp_get("deployment", deployment_id) or deployment
    if environment in GATED and payload.go:
        store.cp_save(
            "approval",
            {
                "project_id": deployment.get("project_id"),
                "subject_type": "deployment",
                "subject_id": deployment_id,
                "decision": "approved",
                "actor": actor,
                "note": payload.evidence[:400],
                "environment": environment,
            },
            project_id=deployment.get("project_id"),
        )

    outcome = payload.outcome.strip().lower()
    if outcome not in {"deployed", "failed", "verified"}:
        raise HTTPException(status_code=422, detail="outcome must be deployed, failed or verified")
    status = {"deployed": "deployed", "verified": "deployed", "failed": "failed"}[outcome]
    updated = store.cp_patch(
        "deployment",
        deployment_id,
        {
            "status": status,
            "decided_by": actor,
            "decided_at": _now(),
            "evidence": payload.evidence[:1000],
        },
    )
    store.add_audit(
        actor, f"deployment.{status}", "deployment", deployment_id,
        redact({"environment": environment, "evidence": payload.evidence[:200]}),
    )
    return updated or deployment


@router.post("/api/v1/deployments/{deployment_id}/rollback", status_code=201)
def rollback(deployment_id: str, payload: RollbackIn, request: Request) -> dict[str, Any]:
    store = _store(request)
    deployment = store.cp_get("deployment", deployment_id)
    if deployment is None:
        raise HTTPException(status_code=404, detail="deployment not found")
    if deployment.get("status") != "deployed":
        raise HTTPException(status_code=409, detail="only a deployed release can be rolled back")
    record = store.cp_save(
        "rollback",
        {
            "project_id": deployment.get("project_id"),
            "deployment_id": deployment_id,
            "environment": deployment.get("environment"),
            "version": deployment.get("version"),
            "reason": payload.reason[:300],
            "status": "requested",
            "requested_by": _actor(request),
        },
        project_id=deployment.get("project_id"),
    )
    store.cp_patch("deployment", deployment_id, {"status": "rolled_back", "rolled_back_at": _now()})
    store.add_audit(_actor(request), "deployment.rollback_requested", "deployment", deployment_id, {"reason": payload.reason[:200]})
    return record


@router.get("/api/v1/cp/metrics")
def metrics(request: Request, project_id: str | None = None) -> dict[str, Any]:
    store = _store(request)
    executions = store.cp_list("execution", project_id=project_id, limit=500)
    by_status: dict[str, int] = {}
    durations: list[float] = []
    for execution in executions:
        by_status[execution.get("status") or "unknown"] = by_status.get(execution.get("status") or "unknown", 0) + 1
        started, finished = execution.get("created_at"), execution.get("finished_at")
        if started and finished:
            try:
                delta = datetime.fromisoformat(finished.replace("Z", "+00:00")) - datetime.fromisoformat(
                    started.replace("Z", "+00:00")
                )
                durations.append(delta.total_seconds())
            except ValueError:
                continue

    issues = store.cp_list("jira_issue", project_id=project_id, limit=500)
    issues_by_status: dict[str, int] = {}
    for issue in issues:
        key = issue.get("status") or "unknown"
        issues_by_status[key] = issues_by_status.get(key, 0) + 1

    deployments = store.cp_list("deployment", project_id=project_id, limit=500)
    deployments_by_env: dict[str, int] = {}
    for deployment in deployments:
        key = str(deployment.get("environment") or "?")
        deployments_by_env[key] = deployments_by_env.get(key, 0) + 1

    terminal = sum(count for status, count in by_status.items() if status in {"succeeded", "failed", "cancelled"})
    succeeded = by_status.get("succeeded", 0)
    return {
        "project_id": project_id,
        "executions": {"total": len(executions), "by_status": by_status},
        "success_rate": round(succeeded / terminal, 3) if terminal else None,
        "mean_duration_seconds": round(sum(durations) / len(durations), 1) if durations else None,
        "issues": {"total": len(issues), "by_status": issues_by_status},
        "deployments": {"total": len(deployments), "by_environment": deployments_by_env},
        "proposals": {
            status: len(store.cp_list("proposal", project_id=project_id, status=status, limit=500))
            for status in ("draft", "approved", "materialised", "failed")
        },
        "cost": None,
        "cost_note": "coût non mesuré : le Kanban n'expose pas encore de compteur de tokens par run",
        "generated_at": _now(),
    }
