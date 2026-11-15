"""Projects, backlog proposals and Jira materialisation (control plane, modules A/B).

Flow implemented here:

    approved agent specification
        -> backlog proposal (editable, nothing created yet)
        -> human approval (recorded, audited)
        -> idempotent materialisation in Jira (Epic / Story / Subtask)
        -> status synchronisation back into the project view

Every step is separated on purpose: a proposal that has not been approved cannot
reach Jira, and materialisation can be replayed without creating duplicates.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import cp_jobs, jira, jira_write
from . import security
from .redaction import redact

router = APIRouter(tags=["control-plane"])

PROJECT_KEY = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")
JIRA_KEY = re.compile(r"^[A-Z][A-Z0-9]*-\d{1,6}$")
PROPOSAL_STATUSES = {"draft", "approved", "materialising", "materialised", "failed"}
ITEM_TYPES = {"Epic", "Story", "Task", "Subtask"}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _store(request: Request):
    return request.app.state.store


def _actor(request: Request) -> str:
    # verified identity only: the session cookie, never a client-supplied header
    return security.actor_of(request)[:80]


def _audit(request: Request, action: str, entity_id: str, payload: dict[str, Any], entity_type: str = "project") -> None:
    _store(request).add_audit(_actor(request), action, entity_type, entity_id, redact(payload))


def _project_or_404(request: Request, project_id: str) -> dict[str, Any]:
    project = _store(request).cp_get("project", project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clean(value: Any, limit: int = 400) -> str:
    return " ".join(str(value or "").split())[:limit]


def _as_list(value: Any, limit: int = 20) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    return [_clean(v, 240) for v in value if _clean(v, 240)][:limit]


# --------------------------------------------------------------------------
# proposal generation (pure function, no I/O)
# --------------------------------------------------------------------------
def build_proposal(spec: dict[str, Any], project_key: str) -> list[dict[str, Any]]:
    """Turn an approved specification into editable Jira items.

    Deterministic: same specification, same items, same order. The epic carries the
    business objective; each story carries its acceptance criteria, its tasks become
    subtasks, and the dependency chain of the specification is preserved.
    """
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else spec
    payload = payload or {}
    backlog = payload.get("backlog") or {}
    epic = backlog.get("epic") or {}
    stories = backlog.get("stories") or []
    if not stories:
        raise HTTPException(status_code=422, detail="the specification has no backlog stories to propose")

    items: list[dict[str, Any]] = [
        {
            "key": "EPIC",
            "type": "Epic",
            "summary": _clean(epic.get("title") or payload.get("name") or "Epic", 180),
            "description": _clean(epic.get("summary") or payload.get("objective") or "", 1200),
            "labels": ["control-plane"],
            "parent": None,
            "acceptance": [],
            "tasks": [],
        }
    ]
    for story in stories:
        key = _clean(story.get("key") or f"S{len(items)}", 12) or f"S{len(items)}"
        acceptance = story.get("acceptance") or []
        criteria = []
        for rule in acceptance:
            if isinstance(rule, dict):
                criteria.append(
                    " ".join(
                        part
                        for part in (
                            f"Étant donné {rule.get('given')}" if rule.get("given") else "",
                            f"quand {rule.get('when')}" if rule.get("when") else "",
                            f"alors {rule.get('then')}" if rule.get("then") else "",
                        )
                        if part
                    )
                )
        items.append(
            {
                "key": key,
                "type": "Story",
                "summary": _clean(story.get("title") or "Story", 180),
                "description": _clean(
                    f"En tant que {story.get('as_a','utilisateur')}, je veux {story.get('i_want','')} "
                    f"afin de {story.get('so_that','')}.",
                    1200,
                ),
                "labels": ["control-plane"],
                "parent": "EPIC",
                "acceptance": criteria[:10],
                "tasks": _as_list(story.get("tasks"), 10),
                "depends_on": _as_list(story.get("depends_on"), 10),
                "definition_of_done": _as_list(story.get("definition_of_done"), 10),
            }
        )
        for index, task in enumerate(_as_list(story.get("tasks"), 10), start=1):
            items.append(
                {
                    "key": f"{key}-{index}",
                    "type": "Subtask",
                    "summary": _clean(task, 180),
                    "description": _clean(f"Sous-tâche de {key} — {story.get('title','')}", 600),
                    "labels": ["control-plane"],
                    "parent": key,
                    "acceptance": [],
                    "tasks": [],
                }
            )
    return items


def _item_description(item: dict[str, Any], project_key: str, proposal_id: str) -> str:
    lines = [item.get("description") or ""]
    if item.get("acceptance"):
        lines.append("Critères d'acceptation :")
        lines.extend(f"- {c}" for c in item["acceptance"])
    if item.get("definition_of_done"):
        lines.append("Définition de terminé :")
        lines.extend(f"- {c}" for c in item["definition_of_done"])
    lines.append(f"Projet Camilia : {project_key} · proposition {proposal_id[:8]}")
    return "\n".join(line for line in lines if line)


# --------------------------------------------------------------------------
# schemas
# --------------------------------------------------------------------------
class ProjectIn(BaseModel):
    key: str = Field(min_length=2, max_length=10)
    name: str = Field(min_length=2, max_length=120)
    business_request: str = Field(default="", max_length=4000)
    objectives: list[str] = Field(default_factory=list)
    kpis: list[str] = Field(default_factory=list)
    stakeholders: list[str] = Field(default_factory=list)
    scope: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    jira_project: str = Field(default="SCRUM", max_length=10)
    github_repo: str = Field(default="", max_length=120)


class ProjectPatch(BaseModel):
    name: str | None = None
    business_request: str | None = None
    objectives: list[str] | None = None
    kpis: list[str] | None = None
    stakeholders: list[str] | None = None
    scope: list[str] | None = None
    constraints: list[str] | None = None
    risks: list[str] | None = None
    status: str | None = None


class ProposalIn(BaseModel):
    spec_id: str | None = None
    title: str | None = None


class ProposalPatch(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)


class DecisionIn(BaseModel):
    actor: str | None = None
    note: str = ""


# --------------------------------------------------------------------------
# projects
# --------------------------------------------------------------------------
@router.get("/api/v1/projects")
def list_projects(request: Request) -> dict[str, Any]:
    rows = _store(request).cp_list("project", limit=100)
    out = []
    for project in rows:
        out.append(
            {
                **project,
                "counts": {
                    "proposals": len(_store(request).cp_list("proposal", project_id=project["id"], limit=200)),
                    "executions": len(_store(request).cp_list("execution", project_id=project["id"], limit=200)),
                    "issues": len(_store(request).cp_list("jira_issue", project_id=project["id"], limit=200)),
                },
            }
        )
    return {"projects": out, "total": len(out)}


@router.post("/api/v1/projects", status_code=201)
def create_project(payload: ProjectIn, request: Request) -> dict[str, Any]:
    key = payload.key.strip().upper()
    if not PROJECT_KEY.match(key):
        raise HTTPException(status_code=422, detail="key must be 2-10 uppercase alphanumerics starting with a letter")
    existing = _store(request).cp_list("project", where={"key": key}, limit=1)
    if existing:
        raise HTTPException(status_code=409, detail=f"project {key} already exists")

    doc = {
        "key": key,
        "name": _clean(payload.name, 120),
        "business_request": (payload.business_request or "")[:4000],
        "objectives": _as_list(payload.objectives),
        "kpis": _as_list(payload.kpis),
        "stakeholders": _as_list(payload.stakeholders),
        "scope": _as_list(payload.scope),
        "constraints": _as_list(payload.constraints),
        "risks": _as_list(payload.risks),
        "jira_project": (payload.jira_project or "SCRUM").strip().upper(),
        "github_repo": _clean(payload.github_repo, 120),
        "status": "draft",
        "stage": "demande",
    }
    project = _store(request).cp_save("project", doc)
    _audit(request, "project.created", project["id"], {"key": key})
    return project


@router.get("/api/v1/projects/{project_id}")
def get_project(project_id: str, request: Request) -> dict[str, Any]:
    store = _store(request)
    project = _project_or_404(request, project_id)
    return {
        "project": project,
        "proposals": store.cp_list("proposal", project_id=project_id, limit=20),
        "executions": store.cp_list("execution", project_id=project_id, limit=20),
        "issues": store.cp_list("jira_issue", project_id=project_id, limit=200),
        "approvals": store.cp_list("approval", project_id=project_id, limit=50),
        "deployments": store.cp_list("deployment", project_id=project_id, limit=50),
        "audit": store.list_audit(project_id, limit=30),
    }


@router.patch("/api/v1/projects/{project_id}")
def patch_project(project_id: str, payload: ProjectPatch, request: Request) -> dict[str, Any]:
    project = _project_or_404(request, project_id)
    changes: dict[str, Any] = {}
    data = payload.model_dump(exclude_unset=True)
    for field in ("name", "business_request", "status"):
        if field in data and data[field] is not None:
            changes[field] = _clean(data[field], 4000 if field == "business_request" else 120)
    for field in ("objectives", "kpis", "stakeholders", "scope", "constraints", "risks"):
        if field in data and data[field] is not None:
            changes[field] = _as_list(data[field])
    if not changes:
        return project
    updated = _store(request).cp_patch("project", project_id, changes)
    _audit(request, "project.updated", project_id, {"fields": sorted(changes)})
    return updated or project


# --------------------------------------------------------------------------
# proposals
# --------------------------------------------------------------------------
@router.get("/api/v1/projects/{project_id}/proposals")
def list_proposals(project_id: str, request: Request) -> dict[str, Any]:
    _project_or_404(request, project_id)
    rows = _store(request).cp_list("proposal", project_id=project_id, limit=50)
    return {"proposals": rows, "total": len(rows)}


@router.post("/api/v1/projects/{project_id}/proposals", status_code=201)
def create_proposal(project_id: str, payload: ProposalIn, request: Request) -> dict[str, Any]:
    store = _store(request)
    project = _project_or_404(request, project_id)

    spec = store.get_agent_spec(payload.spec_id) if payload.spec_id else None
    if payload.spec_id and spec is None:
        raise HTTPException(status_code=404, detail="specification not found")
    if spec is None:
        specs = [s for s in store.list_agent_specs(limit=50) if (s.get("status") or "") == "approved"]
        spec = specs[0] if specs else None
    if spec is None:
        raise HTTPException(status_code=409, detail="no approved specification to turn into a backlog")
    if (spec.get("status") or "") != "approved":
        raise HTTPException(status_code=409, detail="the specification must be approved before proposing a backlog")

    items = build_proposal(spec, project["key"])
    proposal = store.cp_save(
        "proposal",
        {
            "project_id": project_id,
            "spec_id": str(spec.get("id") or payload.spec_id or ""),
            "title": _clean(payload.title or f"{project['name']} — backlog", 160),
            "jira_project": project.get("jira_project") or "SCRUM",
            "items": items,
            "status": "draft",
            "summary": {
                "epics": sum(1 for i in items if i["type"] == "Epic"),
                "stories": sum(1 for i in items if i["type"] == "Story"),
                "subtasks": sum(1 for i in items if i["type"] == "Subtask"),
            },
        },
        project_id=project_id,
    )
    _audit(request, "proposal.created", proposal["id"], {"items": len(items)}, entity_type="proposal")
    return proposal


@router.get("/api/v1/proposals/{proposal_id}")
def get_proposal(proposal_id: str, request: Request) -> dict[str, Any]:
    proposal = _store(request).cp_get("proposal", proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    proposal["issues"] = _store(request).cp_list(
        "jira_issue", where={"proposal_id": proposal_id}, limit=200
    )
    return proposal


@router.patch("/api/v1/proposals/{proposal_id}")
def patch_proposal(proposal_id: str, payload: ProposalPatch, request: Request) -> dict[str, Any]:
    store = _store(request)
    proposal = store.cp_get("proposal", proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    if proposal["status"] != "draft":
        raise HTTPException(status_code=409, detail=f"proposal is {proposal['status']}, only a draft can be edited")
    if not payload.items:
        raise HTTPException(status_code=422, detail="items must not be empty")

    cleaned: list[dict[str, Any]] = []
    keys: set[str] = set()
    for raw in payload.items[:60]:
        key = _clean(raw.get("key"), 20).upper()
        if not key or key in keys:
            raise HTTPException(status_code=422, detail=f"missing or duplicated item key: {key or '?'}")
        keys.add(key)
        item_type = _clean(raw.get("type"), 20).title()
        if item_type not in ITEM_TYPES:
            raise HTTPException(status_code=422, detail=f"unsupported issue type: {item_type}")
        summary = _clean(raw.get("summary"), 180)
        if len(summary) < 3:
            raise HTTPException(status_code=422, detail=f"item {key} has no usable summary")
        cleaned.append(
            {
                "key": key,
                "type": item_type,
                "summary": summary,
                "description": _clean(raw.get("description"), 1200),
                "labels": _as_list(raw.get("labels"), 6),
                "parent": (_clean(raw.get("parent"), 20).upper() or None),
                "acceptance": _as_list(raw.get("acceptance"), 10),
            }
        )
    for item in cleaned:
        if item["parent"] and item["parent"] not in keys:
            raise HTTPException(status_code=422, detail=f"item {item['key']} points to unknown parent {item['parent']}")

    updated = store.cp_patch(
        "proposal",
        proposal_id,
        {
            "items": cleaned,
            "summary": {
                "epics": sum(1 for i in cleaned if i["type"] == "Epic"),
                "stories": sum(1 for i in cleaned if i["type"] == "Story"),
                "subtasks": sum(1 for i in cleaned if i["type"] == "Subtask"),
            },
        },
    )
    _audit(request, "proposal.edited", proposal_id, {"items": len(cleaned)}, entity_type="proposal")
    return updated or proposal


@router.post("/api/v1/proposals/{proposal_id}/approve")
def approve_proposal(proposal_id: str, payload: DecisionIn, request: Request) -> dict[str, Any]:
    store = _store(request)
    proposal = store.cp_get("proposal", proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    if proposal["status"] != "draft":
        raise HTTPException(status_code=409, detail=f"proposal is already {proposal['status']}")
    actor = _clean(payload.actor or _actor(request), 80)
    if actor in {"", "anonymous"}:
        raise HTTPException(status_code=422, detail="an explicit approver is required")

    approval = store.cp_save(
        "approval",
        {
            "project_id": proposal.get("project_id"),
            "subject_type": "proposal",
            "subject_id": proposal_id,
            "decision": "approved",
            "actor": actor,
            "note": (payload.note or "")[:400],
            "items": len(proposal.get("items") or []),
        },
        project_id=proposal.get("project_id"),
    )
    updated = store.cp_patch(
        "proposal",
        proposal_id,
        {"status": "approved", "approved_by": actor, "approved_at": approval["created_at"]},
    )
    _audit(request, "proposal.approved", proposal_id, {"actor": actor, "items": approval["items"]}, entity_type="proposal")
    return {"proposal": updated, "approval": approval}


@router.post("/api/v1/proposals/{proposal_id}/materialize", status_code=202)
def materialize(proposal_id: str, request: Request) -> dict[str, Any]:
    store = _store(request)
    proposal = store.cp_get("proposal", proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    if proposal["status"] not in {"approved", "materialised", "failed"}:
        raise HTTPException(status_code=409, detail="the proposal must be approved before creating Jira issues")

    writer = jira_write.JiraWriter()
    if not writer.enabled:
        raise HTTPException(
            status_code=409,
            detail="jira writes are disabled on this deployment (JIRA_WRITE_ENABLED)",
        )

    actor = _actor(request)
    project_id = proposal.get("project_id")

    def work() -> dict[str, Any]:
        created: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        known = {doc.get("item_key"): doc for doc in store.cp_list("jira_issue", where={"proposal_id": proposal_id}, limit=200)}
        resolved: dict[str, str] = {}

        ordered = sorted(
            proposal.get("items") or [],
            key=lambda item: {"Epic": 0, "Story": 1, "Task": 2, "Subtask": 3}.get(item.get("type"), 4),
        )
        for item in ordered:
            if item["key"] in known:
                resolved[item["key"]] = known[item["key"]].get("key", "")
                skipped.append({"item_key": item["key"], "key": known[item["key"]].get("key")})
                continue
            parent = None
            if item.get("parent"):
                parent = resolved.get(item["parent"]) or (known.get(item["parent"]) or {}).get("key")
                if not parent:
                    failures.append({"item_key": item["key"], "error": f"parent {item['parent']} not created"})
                    continue
            try:
                result = writer.create_issue(
                    summary=f"[{proposal_id[:8]}] {item['summary']}",
                    issue_type=item["type"],
                    description=_item_description(item, proposal.get("jira_project") or "SCRUM", proposal_id),
                    project=proposal.get("jira_project") or "SCRUM",
                    labels=["control-plane", proposal_id[:8]],
                    parent=parent,
                )
            except (jira_write.JiraWriteDisabled, jira_write.JiraWriteForbidden) as exc:
                failures.append({"item_key": item["key"], "error": str(exc)})
                continue
            except Exception as exc:  # noqa: BLE001 - reported per item
                failures.append({"item_key": item["key"], "error": f"{type(exc).__name__}: {str(exc)[:300]}"})
                continue

            store.cp_save(
                "jira_issue",
                {
                    "project_id": project_id,
                    "proposal_id": proposal_id,
                    "item_key": item["key"],
                    "type": item["type"],
                    "key": result.get("key"),
                    "url": result.get("url"),
                    "summary": item["summary"],
                    "status": "To Do",
                    "created": bool(result.get("created")),
                },
                project_id=project_id,
            )
            resolved[item["key"]] = result.get("key") or ""
            (created if result.get("created") else skipped).append(
                {"item_key": item["key"], "key": result.get("key")}
            )

        status = "materialised" if not failures else ("materialised" if created or skipped else "failed")
        store.cp_patch(
            "proposal",
            proposal_id,
            {
                "status": status,
                "materialized_at": _now(),
                "result": {
                    "created": len(created),
                    "existing": len(skipped),
                    "failed": len(failures),
                    "failures": failures[:20],
                },
            },
        )
        store.add_audit(
            actor,
            "proposal.materialized",
            "proposal",
            proposal_id,
            redact({"created": len(created), "existing": len(skipped), "failed": len(failures)}),
        )
        return {"created": created, "existing": skipped, "failures": failures}

    job = cp_jobs.submit(
        store,
        kind="proposal.materialize",
        subject_type="proposal",
        subject_id=proposal_id,
        project_id=project_id,
        actor=actor,
        fn=work,
    )
    return {"job": job, "poll": f"/api/v1/jobs/{job['id']}"}


@router.post("/api/v1/projects/{project_id}/jira/sync")
def sync_jira(project_id: str, request: Request) -> dict[str, Any]:
    store = _store(request)
    _project_or_404(request, project_id)
    issues = store.cp_list("jira_issue", project_id=project_id, limit=200)
    keys = [doc["key"] for doc in issues if JIRA_KEY.match(str(doc.get("key") or ""))]
    if not keys:
        return {"checked": 0, "updated": 0, "issues": []}
    if not (os.getenv("JIRA_SITE_URL") and os.getenv("JIRA_ACCOUNT_EMAIL") and os.getenv("JIRA_API_TOKEN")):
        raise HTTPException(status_code=409, detail="jira credentials are not configured")

    client = jira.JiraClient(
        os.environ["JIRA_SITE_URL"], os.environ["JIRA_ACCOUNT_EMAIL"], os.environ["JIRA_API_TOKEN"]
    )
    jql = "key in (" + ",".join(keys[:90]) + ")"
    rows = {row["key"]: row for row in client.issues(jql, limit=100)}
    updated = 0
    for doc in issues:
        fresh = rows.get(doc.get("key"))
        if not fresh or (fresh.get("status") or "") == (doc.get("status") or ""):
            continue
        store.cp_patch("jira_issue", doc["id"], {"status": fresh.get("status"), "synced_at": _now()})
        updated += 1
    _audit(request, "jira.synced", project_id, {"checked": len(keys), "updated": updated})
    return {"checked": len(keys), "updated": updated, "issues": list(rows.values())}


@router.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict[str, Any]:
    job = _store(request).cp_get("job", job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


def boot(store) -> None:
    """Called once at startup: seeded environments and orphaned jobs."""
    for name in ("DEV", "QA", "PRE-PROD", "PROD"):
        if not store.cp_list("environment", where={"name": name}, limit=1):
            store.cp_save(
                "environment",
                {"name": name, "gate": name in {"PRE-PROD", "PROD"}, "status": "available"},
                doc_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"camilia-env-{name}")),
            )
    cp_jobs.reap_orphans(store)
