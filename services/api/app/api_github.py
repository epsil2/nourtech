"""GitHub visibility endpoints — read-only by construction."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from .github import GitHubClient, GitHubError, GitHubNotConfigured

router = APIRouter(prefix="/api/v1/integrations/github", tags=["github"])


def _guard(exc: Exception) -> HTTPException:
    if isinstance(exc, GitHubNotConfigured):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, GitHubError):
        return HTTPException(status_code=502, detail=str(exc))
    return HTTPException(status_code=500, detail="unexpected github error")


@router.get("/status")
def status() -> dict[str, Any]:
    client = GitHubClient()
    return {
        "configured": client.configured,
        "repo": client.repo or None,
        "mode": "read-only",
        "token_present": client.token_present,
    }


@router.get("/repo")
def repo() -> dict[str, Any]:
    try:
        return GitHubClient().repo_info()
    except (GitHubNotConfigured, GitHubError) as exc:
        raise _guard(exc) from exc


@router.get("/pulls")
def pulls(
    state: str = Query(default="all", pattern=r"^(open|closed|all)$"),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    try:
        rows = GitHubClient().pulls(state, limit)
    except (GitHubNotConfigured, GitHubError) as exc:
        raise _guard(exc) from exc
    return {"items": rows, "count": len(rows)}


@router.get("/commits")
def commits(limit: int = Query(default=10, ge=1, le=50)) -> dict[str, Any]:
    try:
        rows = GitHubClient().commits(limit)
    except (GitHubNotConfigured, GitHubError) as exc:
        raise _guard(exc) from exc
    return {"items": rows, "count": len(rows)}


@router.get("/checks/{sha}")
def checks(sha: str) -> dict[str, Any]:
    if len(sha) < 7 or len(sha) > 40 or any(c not in "0123456789abcdef" for c in sha.lower()):
        raise HTTPException(status_code=422, detail="sha must be hexadecimal")
    try:
        rows = GitHubClient().checks(sha)
    except (GitHubNotConfigured, GitHubError) as exc:
        raise _guard(exc) from exc
    return {"items": rows, "count": len(rows)}


@router.get("/actions")
def actions(limit: int = Query(default=10, ge=1, le=50)) -> dict[str, Any]:
    try:
        rows = GitHubClient().workflow_runs(limit)
    except (GitHubNotConfigured, GitHubError) as exc:
        raise _guard(exc) from exc
    return {"items": rows, "count": len(rows)}


@router.get("/releases")
def releases(limit: int = Query(default=10, ge=1, le=20)) -> dict[str, Any]:
    try:
        rows = GitHubClient().releases(limit)
    except (GitHubNotConfigured, GitHubError) as exc:
        raise _guard(exc) from exc
    return {"items": rows, "count": len(rows)}


@router.get("/overview")
def overview() -> dict[str, Any]:
    """One call the dashboard can render as a single page."""
    client = GitHubClient()
    try:
        data = {
            "repo": client.repo_info(),
            "pulls": client.pulls("all", 5),
            "commits": client.commits(5),
            "actions": client.workflow_runs(5),
            "releases": client.releases(5),
        }
    except (GitHubNotConfigured, GitHubError) as exc:
        raise _guard(exc) from exc
    return data
