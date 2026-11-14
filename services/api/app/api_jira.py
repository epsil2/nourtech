"""Jira integration endpoints.

Reads (``/status``, ``/schema``, ``/issues``) are always available. Writes are
opt-in per deployment: they only work when ``JIRA_WRITE_ENABLED=true`` and the
target project is in ``JIRA_WRITE_PROJECT`` (see :mod:`app.jira_write`).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import jira_write
from .jira import JiraClient, JiraError, JiraNotConfigured
from .jira_write import JiraWriteDisabled, JiraWriteForbidden

router = APIRouter(prefix="/api/v1/integrations/jira", tags=["jira"])


class CreateIssueIn(BaseModel):
    summary: str = Field(min_length=3, max_length=180)
    issue_type: str = Field(default="Story", max_length=40)
    description: str | None = Field(default=None, max_length=4000)
    project: str | None = Field(default=None, min_length=2, max_length=20)
    labels: list[str] = Field(default_factory=list, max_length=10)
    parent: str | None = Field(default=None, max_length=40)


class CommentIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class TransitionIn(BaseModel):
    target: str = Field(min_length=1, max_length=60)


def _write_guard(exc: Exception) -> HTTPException:
    if isinstance(exc, (JiraWriteDisabled, JiraWriteForbidden)):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, JiraNotConfigured):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, JiraError):
        return HTTPException(status_code=502, detail=str(exc))
    return HTTPException(status_code=500, detail="unexpected jira error")


def _audit(request: Request, actor: str | None, action: str, key: str, payload: dict[str, Any]) -> None:
    store = getattr(request.app.state, "store", None)
    if store is not None:
        store.add_audit((actor or "anonymous")[:120], action, "jira_issue", key, payload)


@router.get("/status")
def status() -> dict[str, Any]:
    """What this deployment may do with Jira. Never echoes the token."""
    return jira_write.status()


@router.get("/schema")
def schema(
    project: str = Query(default="SCRUM", min_length=2, max_length=20),
    board: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    client = JiraClient()
    try:
        result = client.schema(project, board)
    except JiraNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except JiraError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    result["write_enabled"] = jira_write.JiraWriter().enabled
    return result


@router.get("/issues")
def issues(
    jql: str = Query(min_length=6, max_length=400, pattern=r"^[^;]+$"),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    client = JiraClient()
    try:
        rows = client.issues(jql, limit)
    except JiraNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except JiraError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"items": rows, "count": len(rows)}


@router.post("/issues", status_code=201)
def create_issue(
    body: CreateIssueIn, request: Request, x_actor: str | None = Header(default=None)
) -> dict[str, Any]:
    """Create a labelled issue. Idempotent on (project, summary)."""
    writer = jira_write.JiraWriter()
    try:
        row = writer.create_issue(
            body.summary, body.issue_type, body.description, body.project, body.labels, body.parent
        )
    except Exception as exc:  # noqa: BLE001 - mapped to HTTP below
        raise _write_guard(exc) from exc
    _audit(request, x_actor, "jira.issue.created" if row["created"] else "jira.issue.reused",
           row["key"], {"summary": body.summary, "type": body.issue_type,
                        "model": jira_write.MARKER_LABEL, "project": body.project})
    return row


@router.get("/issues/{key}/transitions")
def transitions(key: str) -> dict[str, Any]:
    """Available workflow transitions for one issue."""
    client = JiraClient()
    try:
        rows = client.transitions(jira_write.JiraWriter().check_project(key))
    except (JiraWriteForbidden, ValueError) as exc:
        raise _write_guard(exc) from exc
    except JiraNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except JiraError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"items": rows, "count": len(rows)}


@router.post("/issues/{key}/comment", status_code=201)
def add_comment(
    key: str, body: CommentIn, request: Request, x_actor: str | None = Header(default=None)
) -> dict[str, Any]:
    """Append a comment (Atlassian Document Format is built server-side)."""
    try:
        row = jira_write.JiraWriter().comment(key, body.text)
    except Exception as exc:  # noqa: BLE001
        raise _write_guard(exc) from exc
    _audit(request, x_actor, "jira.issue.commented", row["key"], {"chars": len(body.text)})
    return row


@router.post("/issues/{key}/transition", status_code=200)
def transition(
    key: str, body: TransitionIn, request: Request, x_actor: str | None = Header(default=None)
) -> dict[str, Any]:
    """Move an issue along its workflow by transition id, name or target status."""
    try:
        row = jira_write.JiraWriter().transition(key, body.target)
    except Exception as exc:  # noqa: BLE001
        raise _write_guard(exc) from exc
    _audit(request, x_actor, "jira.issue.transitioned", row["key"],
           {"transition": row["transition"], "to": row["to"]})
    return row
