"""Jira board reads: backlog and sprints for the dashboard.

Read-only, same credentials as the rest of the Jira integration. Writes stay in
``api_jira`` behind the opt-in guard; nothing here can create or move an issue.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from .jira import JiraClient, JiraError, JiraNotConfigured

router = APIRouter(prefix="/api/v1/integrations/jira/board", tags=["jira"])


def _guard(exc: Exception) -> HTTPException:
    if isinstance(exc, JiraNotConfigured):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, JiraError):
        return HTTPException(status_code=502, detail=str(exc))
    return HTTPException(status_code=500, detail="unexpected jira error")


@router.get("/backlog")
def backlog(
    project: str = Query(default="SCRUM", min_length=2, max_length=20, pattern=r"^[A-Z][A-Z0-9]*$"),
    limit: int = Query(default=60, ge=1, le=100),
) -> dict[str, Any]:
    """Issues of one project, newest first, with the fields a board actually shows."""
    jql = (
        f"project = {project} ORDER BY status ASC, created DESC"
    )
    client = JiraClient()
    try:
        items = client.board_issues(jql, limit)
    except (JiraNotConfigured, JiraError) as exc:
        raise _guard(exc) from exc
    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for row in items:
        by_status[row["status"] or "—"] = by_status.get(row["status"] or "—", 0) + 1
        by_type[row["type"] or "—"] = by_type.get(row["type"] or "—", 0) + 1
    return {
        "project": project,
        "items": items,
        "count": len(items),
        "by_status": dict(sorted(by_status.items())),
        "by_type": dict(sorted(by_type.items())),
    }


@router.get("/sprints")
def sprints(
    board: int = Query(default=1, ge=1),
    project: str = Query(default="SCRUM", min_length=2, max_length=20, pattern=r"^[A-Z][A-Z0-9]*$"),
) -> dict[str, Any]:
    """Sprints of one board plus how many issues of the project are in each."""
    client = JiraClient()
    try:
        rows = client.sprints(board)
        issues = client.board_issues(
            f"project = {project} AND sprint IS NOT EMPTY ORDER BY updated DESC", 100
        )
    except (JiraNotConfigured, JiraError) as exc:
        raise _guard(exc) from exc

    counts: dict[str, int] = {}
    for issue in issues:
        for sprint in issue.get("sprints", []) or []:
            name = sprint.get("name") or "—"
            counts[name] = counts.get(name, 0) + 1

    for row in rows:
        row["issues"] = counts.get(row.get("name") or "", 0)
    active = [r for r in rows if r.get("state") == "active"]
    return {
        "board": board,
        "project": project,
        "items": rows,
        "count": len(rows),
        "active": active[0] if active else None,
    }
