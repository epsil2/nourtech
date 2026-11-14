"""Jira integration endpoints — strictly read-only (SCRUM-24, SCRUM-36)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from .jira import JiraClient, JiraError, JiraNotConfigured

router = APIRouter(prefix="/api/v1/integrations/jira", tags=["jira"])


@router.get("/status")
def status() -> dict[str, Any]:
    """Is the integration configured? Never echoes the token."""
    client = JiraClient()
    return {
        "configured": client.configured,
        "site": client.base_url or None,
        "read_only": True,
    }


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
    return {"items": rows, "count": len(rows), "read_only": True}
