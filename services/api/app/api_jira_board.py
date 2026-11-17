"""Jira board reads: projects, backlog, sprints and the facets the dashboard filters on.

Read-only, same credentials as the rest of the Jira integration. Writes stay in
``api_jira`` behind the opt-in guard; nothing here can create or move an issue.

The backlog endpoint answers one question — *what is left, given these filters?* —
so the filtering happens here rather than in the browser: the dashboard builds four
drop-downs (project, sprint, feature, user story) from the ``facets`` block, and every
selection comes back as a new ``items`` list. Facets are always computed on the whole
project, never on the filtered subset, otherwise picking a sprint would empty the
sprint list and the user could not come back.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from .jira import JiraClient, JiraError, JiraNotConfigured

router = APIRouter(prefix="/api/v1/integrations/jira/board", tags=["jira"])

PROJECT_PATTERN = r"^[A-Z][A-Z0-9]*$"
ISSUE_KEY_PATTERN = r"^[A-Z][A-Z0-9]*-\d+$"
# Jira hierarchy level 1: the container issue types a team calls an epic or a feature
FEATURE_TYPES = {"epic", "feature", "initiative", "theme"}
SUB_TYPES = {"subtask", "sub-task"}
NO_SPRINT = "none"
MAX_ITEMS = 300
CACHE_TTL = float(os.getenv("JIRA_BOARD_CACHE_TTL", "45") or 0)

# one Jira read per project per CACHE_TTL seconds: moving a drop-down must not
# hammer Jira, and the facets stay stable while the user narrows a selection
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_cache_lock = threading.Lock()


def _guard(exc: Exception) -> HTTPException:
    if isinstance(exc, JiraNotConfigured):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, JiraError):
        return HTTPException(status_code=502, detail=str(exc))
    return HTTPException(status_code=500, detail="unexpected jira error")


def _project_issues(project: str, limit: int, fresh: bool = False) -> list[dict[str, Any]]:
    """Every issue of one project, newest first, cached for a few seconds."""
    key = f"{project}:{limit}"
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and not fresh and now - hit[0] < CACHE_TTL:
            return hit[1]
    items = JiraClient().board_issues(
        f"project = {project} ORDER BY status ASC, created DESC", limit
    )
    with _cache_lock:
        _cache[key] = (now, items)
    return items


def _sprints_of(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for issue in items:
        for sprint in issue.get("sprints") or []:
            sid = str(sprint.get("id") or "")
            if not sid:
                continue
            row = found.setdefault(
                sid,
                {
                    "id": sid,
                    "name": sprint.get("name") or sid,
                    "state": sprint.get("state") or "unknown",
                    "issues": 0,
                },
            )
            row["issues"] += 1
    order = {"active": 0, "future": 1, "closed": 2}
    return sorted(found.values(), key=lambda s: (order.get(s["state"], 3), s["name"]))


def _facets(items: list[dict[str, Any]]) -> dict[str, Any]:
    """The drop-down options, always computed on the unfiltered project."""
    children: dict[str, int] = {}
    for issue in items:
        parent = issue.get("parent")
        if parent:
            children[parent] = children.get(parent, 0) + 1

    features: list[dict[str, Any]] = []
    stories: list[dict[str, Any]] = []
    for issue in items:
        kind = (issue.get("type") or "").lower()
        summary = issue.get("summary") or ""
        # an epic/feature by type, or a container issue some other type is filed under
        is_feature = kind in FEATURE_TYPES or (
            bool(children.get(issue.get("key") or ""))
            and kind not in {"story", "task", "bug"} | SUB_TYPES
        )
        if is_feature:
            features.append(
                {
                    "key": issue["key"],
                    "summary": summary,
                    "type": issue.get("type"),
                    "status": issue.get("status"),
                    "issues": children.get(issue["key"], 0),
                }
            )
        elif kind not in SUB_TYPES:
            stories.append(
                {
                    "key": issue["key"],
                    "summary": summary,
                    "type": issue.get("type"),
                    "status": issue.get("status"),
                    "parent": issue.get("parent"),
                }
            )

    features.sort(key=lambda f: (f["key"].split("-")[0], int(f["key"].split("-")[1])))
    return {
        "sprints": _sprints_of(items),
        "features": features,
        "stories": stories,
        "backlog_only": sum(1 for i in items if not (i.get("sprints") or [])),
    }


def _matches(issue: dict[str, Any], sprint: str | None, feature: str | None,
             story: str | None, needle: str | None) -> bool:
    keys = str(issue.get("key") or "").upper()
    if sprint:
        ids = [str(s.get("id")) for s in issue.get("sprints") or []]
        if sprint == NO_SPRINT:
            if ids:
                return False
        elif sprint not in ids:
            return False
    if feature and not (keys == feature or str(issue.get("parent") or "").upper() == feature):
        return False
    if story and not (keys == story or str(issue.get("parent") or "").upper() == story):
        return False
    if needle:
        haystack = " ".join(
            [
                keys,
                str(issue.get("summary") or ""),
                str(issue.get("status") or ""),
                str(issue.get("assignee") or ""),
                str(issue.get("type") or ""),
                str(issue.get("parent") or ""),
                " ".join(str(s.get("name") or "") for s in issue.get("sprints") or []),
            ]
        ).lower()
        if needle not in haystack:
            return False
    return True


@router.get("/projects")
def projects() -> dict[str, Any]:
    """Projects the dashboard may filter on, with the agile board behind each one."""
    client = JiraClient()
    try:
        rows = client.projects()
        try:
            boards = client.boards()
        except (JiraNotConfigured, JiraError):
            boards = []  # a project without an agile board still lists its backlog
    except (JiraNotConfigured, JiraError) as exc:
        raise _guard(exc) from exc

    by_project = {b["project_key"]: b["id"] for b in boards if b.get("project_key")}
    items = [
        {
            "key": p.get("key"),
            "name": p.get("name"),
            "style": p.get("style"),
            "board": by_project.get(p.get("key")),
        }
        for p in rows
        if p.get("key")
    ]
    items.sort(key=lambda p: (p["name"] or "").lower())
    return {"items": items, "count": len(items)}


@router.get("/backlog")
def backlog(
    project: str = Query(default="SCRUM", min_length=2, max_length=20, pattern=PROJECT_PATTERN),
    sprint: str | None = Query(default=None, max_length=32),
    feature: str | None = Query(default=None, max_length=32, pattern=ISSUE_KEY_PATTERN),
    story: str | None = Query(default=None, max_length=32, pattern=ISSUE_KEY_PATTERN),
    q: str | None = Query(default=None, max_length=120),
    refresh: bool = Query(default=False),
    limit: int = Query(default=MAX_ITEMS, ge=1, le=MAX_ITEMS * 2),
) -> dict[str, Any]:
    """Issues of one project, filtered by the dashboard's four drop-downs."""
    sprint = (sprint or "").strip() or None
    feature = (feature or "").strip().upper() or None
    story = (story or "").strip().upper() or None
    needle = (q or "").strip().lower() or None

    try:
        items = _project_issues(project, limit, fresh=refresh)
    except (JiraNotConfigured, JiraError) as exc:
        raise _guard(exc) from exc

    facets = _facets(items)
    filtered = [
        i for i in items if _matches(i, sprint, feature, story, needle)
    ]

    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for row in filtered:
        by_status[row.get("status") or "—"] = by_status.get(row.get("status") or "—", 0) + 1
        by_type[row.get("type") or "—"] = by_type.get(row.get("type") or "—", 0) + 1

    return {
        "project": project,
        "items": filtered,
        "count": len(filtered),
        "total": len(items),
        "truncated": len(items) >= limit,
        "by_status": dict(sorted(by_status.items())),
        "by_type": dict(sorted(by_type.items())),
        "facets": facets,
        "filters": {
            "sprint": sprint,
            "feature": feature,
            "story": story,
            "q": needle,
            "active": sum(1 for v in (sprint, feature, story, needle) if v),
        },
    }


@router.get("/sprints")
def sprints(
    project: str = Query(default="SCRUM", min_length=2, max_length=20, pattern=PROJECT_PATTERN),
    sprint: str | None = Query(default=None, max_length=32),
    feature: str | None = Query(default=None, max_length=32, pattern=ISSUE_KEY_PATTERN),
    story: str | None = Query(default=None, max_length=32, pattern=ISSUE_KEY_PATTERN),
    q: str | None = Query(default=None, max_length=120),
    board: int | None = Query(default=None, ge=1),
    refresh: bool = Query(default=False),
    limit: int = Query(default=MAX_ITEMS, ge=1, le=MAX_ITEMS * 2),
) -> dict[str, Any]:
    """Sprints of the project's board, counted on the current selection.

    The Sprints page asks a narrower question than the backlog — *which sprints
    still carry work, and how much of it?* — so each row keeps its unfiltered
    size (``all_issues``) next to the count that matches the selection
    (``issues``), and the tickets of the chosen sprint come back in ``tickets``.
    Rows are never dropped: an empty sprint still tells the operator the sprint
    exists and was planned.
    """
    sprint = (sprint or "").strip() or None
    feature = (feature or "").strip().upper() or None
    story = (story or "").strip().upper() or None
    needle = (q or "").strip().lower() or None

    try:
        issues = _project_issues(project, limit, fresh=refresh)
        client = JiraClient()
        board_id = board
        if board_id is None:
            try:
                boards = client.boards()
                board_id = next(
                    (int(b["id"]) for b in boards if b.get("project_key") == project), None
                )
            except (JiraNotConfigured, JiraError, AttributeError, TypeError, ValueError):
                board_id = 1  # a project without an agile board still has sprints on board 1
        rows: list[dict[str, Any]] = []
        board_supports_sprints = True
        if board_id:
            try:
                rows = client.sprints(board_id)
            except JiraError as exc:
                # A Kanban/team-managed board answers 400 "The board does not support
                # sprints". That is a property of the board, not a credential fault:
                # the tickets still carry their sprint field, so the page is rebuilt
                # from the facets instead of failing whole with a misleading 502.
                if exc.status != 400:
                    raise
                board_supports_sprints = False
    except (JiraNotConfigured, JiraError) as exc:
        raise _guard(exc) from exc

    facets = _facets(issues)
    # the sprint filter must not narrow the sprint counts: if it did, choosing a
    # sprint would zero every other row and the operator could not switch back
    base = [i for i in issues if _matches(i, None, feature, story, needle)]
    selected = [i for i in issues if _matches(i, sprint, feature, story, needle)]

    matching: dict[str, int] = {}
    done: dict[str, int] = {}
    for issue in base:
        closed = (issue.get("status") or "").strip().lower() in {"done", "closed", "terminé", "terminée"}
        for row in issue.get("sprints") or []:
            sid = str(row.get("id") or "")
            if not sid:
                continue
            matching[sid] = matching.get(sid, 0) + 1
            if closed:
                done[sid] = done.get(sid, 0) + 1

    by_id = {f["id"]: f for f in facets["sprints"]}
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        sid = str(row.get("id") or "")
        seen.add(sid)
        facet = by_id.get(sid) or {}
        items.append(
            {
                **row,
                "issues": matching.get(sid, 0),
                "all_issues": facet.get("issues", 0),
                "done": done.get(sid, 0),
            }
        )
    # a sprint the board no longer returns but whose tickets still point at it
    for facet in facets["sprints"]:
        if facet["id"] in seen:
            continue
        items.append(
            {
                "id": facet["id"],
                "name": facet["name"],
                "state": facet["state"],
                "start": None,
                "end": None,
                "issues": matching.get(facet["id"], 0),
                "all_issues": facet["issues"],
                "done": done.get(facet["id"], 0),
            }
        )

    order = {"active": 0, "future": 1, "closed": 2}
    items.sort(
        key=lambda s: (
            order.get(str(s.get("state") or ""), 3),
            str(s.get("start") or ""),
            str(s.get("name") or ""),
        )
    )
    active = next((s for s in items if s.get("state") == "active"), None)
    return {
        "project": project,
        "board": board_id,
        "board_supports_sprints": board_supports_sprints,
        "items": items,
        "count": len(items),
        "active": active,
        "total": len(issues),
        "matching": len(selected),
        "selected": len(base),
        "unscheduled": sum(1 for i in base if not (i.get("sprints") or [])),
        "tickets": selected,
        "facets": facets,
        "filters": {
            "sprint": sprint,
            "feature": feature,
            "story": story,
            "q": needle,
            "active": sum(1 for v in (sprint, feature, story, needle) if v),
        },
    }

