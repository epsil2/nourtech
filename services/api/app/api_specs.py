"""Agent specification endpoints (Sprint 2).

The wizard must be able to show a specification *before* anything exists in the
database: ``/draft`` is pure, it stores nothing and creates nothing. Persisting a
draft, then approving it, are two separate audited steps — approval is what turns
a proposal into a registered agent. Jira backlog creation is Sprint 3 and is
deliberately absent here.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import specification as spec_generator
from .redaction import redact

router = APIRouter(prefix="/api/v1/specifications", tags=["specifications"])


class IntakeIn(BaseModel):
    name: str = Field(default="", max_length=120)
    objective: str = Field(min_length=10, max_length=2000)
    problem: str = Field(default="", max_length=2000)
    outcomes: list[str] = Field(default_factory=list, max_length=12)
    constraints: list[str] = Field(default_factory=list, max_length=12)
    skills: list[str] = Field(default_factory=list, max_length=12)
    tools: list[str] = Field(default_factory=list, max_length=12)
    permissions: list[str] = Field(default_factory=list, max_length=8)
    kpis: list[str] = Field(default_factory=list, max_length=12)
    llm_provider: str | None = Field(default=None, max_length=80)
    llm_model: str | None = Field(default=None, max_length=120)


def _store(request: Request):
    return request.app.state.store


@router.get("/catalog")
def catalog() -> dict[str, Any]:
    """What the wizard may offer. Single source of truth, served by the API."""
    return {
        "skills": [
            {"id": key, "label": value["label"], "story": value["story"]}
            for key, value in spec_generator.SKILL_CATALOG.items()
        ],
        "tools": [{"id": key, "label": value} for key, value in spec_generator.TOOL_CATALOG.items()],
        "permissions": [{"id": key, "label": value} for key, value in spec_generator.PERMISSION_CATALOG.items()],
        "human_gates": [
            {"id": gate, "label": label, "button": button} for gate, label, button in spec_generator.HUMAN_GATES
        ],
    }


@router.post("/draft")
def draft(body: IntakeIn) -> dict[str, Any]:
    """Preview: generates and returns the specification, stores nothing."""
    try:
        return spec_generator.draft(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("", status_code=201)
def create(body: IntakeIn, request: Request, x_actor: str | None = Header(default=None)) -> dict[str, Any]:
    store = _store(request)
    try:
        document = spec_generator.draft(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    row = store.create_agent_spec(document["slug"], document)
    store.add_audit(
        (x_actor or "anonymous")[:120],
        "specification.created",
        "agent_spec",
        row["id"],
        redact({"slug": document["slug"], "objective": document["objective"], "skills": document["skills"]}),
    )
    return row


@router.get("")
def list_specs(request: Request, limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    rows = _store(request).list_agent_specs(limit)
    return {"items": rows, "count": len(rows)}


@router.get("/{spec_id}")
def get_spec(spec_id: str, request: Request) -> dict[str, Any]:
    row = _store(request).get_agent_spec(spec_id)
    if row is None:
        raise HTTPException(status_code=404, detail="specification not found")
    return row


@router.post("/{spec_id}/approve")
def approve(spec_id: str, request: Request, x_actor: str | None = Header(default=None)) -> dict[str, Any]:
    """Human approval: registers the agent, records who approved. Idempotent."""
    store = _store(request)
    current = store.get_agent_spec(spec_id)
    if current is None:
        raise HTTPException(status_code=404, detail="specification not found")
    if current["status"] == "approved":
        return current

    actor = (x_actor or "anonymous")[:120]
    payload = current["payload"]
    agent = None
    existing = {a["name"] for a in store.list_agents()}
    if payload["slug"] not in existing:
        agent = store.create_agent(payload["slug"], payload.get("objective", ""))
    else:
        agent = next(a for a in store.list_agents() if a["name"] == payload["slug"])

    row = store.approve_agent_spec(spec_id, agent["id"], actor)
    store.add_audit(
        actor,
        "specification.approved",
        "agent_spec",
        spec_id,
        redact({"agent": agent["name"], "from": current["status"], "to": "approved"}),
    )
    store.add_audit(actor, "agent.registered", "agent", agent["id"], redact({"from": "specification", "slug": agent["name"]}))
    return row
