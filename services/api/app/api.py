"""Factory API — agents, runs, audit trail, metrics (SCRUM-31, SCRUM-32, SCRUM-41).

Every mutating call writes one audit row whose payload has already been through
``redaction.redact``: the store can therefore be read back safely, even if a
caller sends a token in a payload.
"""
from __future__ import annotations

import os
import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .redaction import redact

router = APIRouter(prefix="/api/v1")
STARTED = time.time()

# Allowed lifecycle. Sprint 2 covers the plain run states; the checkpoint and
# approval states are added by SCRUM-23 in Sprint 4.
TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "cancelled"},
    "running": {"succeeded", "failed", "cancelled"},
    "succeeded": set(),
    "failed": set(),
    "cancelled": set(),
}


class AgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9._-]+$")
    description: str = Field(default="", max_length=2000)


class AgentPatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9._-]+$")
    description: str | None = Field(default=None, max_length=2000)


class RunIn(BaseModel):
    objective: str = Field(min_length=3, max_length=4000)
    agent_id: str | None = None


class StatusIn(BaseModel):
    status: str


def _store(request: Request):
    return request.app.state.store


def _actor(x_actor: str | None) -> str:
    return (x_actor or "anonymous")[:120]


def _audit(store, actor: str, action: str, entity_type: str, entity_id: str, payload: dict[str, Any]) -> None:
    store.add_audit(actor, action, entity_type, entity_id, redact(payload))


@router.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "service": "factory-api", "version": os.getenv("FACTORY_VERSION", "0.2.0")}


@router.post("/agents", status_code=201)
def create_agent(body: AgentIn, request: Request, x_actor: str | None = Header(default=None)) -> dict[str, Any]:
    store = _store(request)
    try:
        row = store.create_agent(body.name, body.description)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _audit(store, _actor(x_actor), "agent.created", "agent", row["id"], {"name": row["name"]})
    return row


@router.get("/agents")
def list_agents(request: Request) -> dict[str, Any]:
    rows = _store(request).list_agents()
    return {"items": rows, "count": len(rows)}


@router.get("/agents/{agent_id}")
def get_agent(agent_id: str, request: Request) -> dict[str, Any]:
    row = _store(request).get_agent(agent_id)
    if row is None:
        raise HTTPException(status_code=404, detail="agent not found")
    return row


@router.patch("/agents/{agent_id}")
def update_agent(
    agent_id: str,
    body: AgentPatchIn,
    request: Request,
    x_actor: str | None = Header(default=None),
) -> dict[str, Any]:
    """Rename an agent or rewrite its description. Nothing else is editable here."""
    store = _store(request)
    if body.name is None and body.description is None:
        raise HTTPException(status_code=422, detail="nothing to update")
    try:
        row = store.update_agent(agent_id, body.name, body.description)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="agent not found")
    _audit(
        store,
        _actor(x_actor),
        "agent.updated",
        "agent",
        row["id"],
        {"name": row["name"], "fields": [k for k in ("name", "description") if getattr(body, k) is not None]},
    )
    return row


@router.post("/runs", status_code=201)
def create_run(body: RunIn, request: Request, x_actor: str | None = Header(default=None)) -> dict[str, Any]:
    store = _store(request)
    if body.agent_id is not None and store.get_agent(body.agent_id) is None:
        raise HTTPException(status_code=422, detail="unknown agent_id")
    row = store.create_run(body.agent_id, body.objective)
    _audit(
        store,
        _actor(x_actor),
        "run.created",
        "run",
        row["id"],
        {"objective": row["objective"], "agent_id": row["agent_id"], "status": row["status"]},
    )
    return row


@router.get("/runs")
def list_runs(
    request: Request,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    rows = _store(request).list_runs(status, limit)
    return {"items": rows, "count": len(rows)}


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request) -> dict[str, Any]:
    row = _store(request).get_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="run not found")
    return row


@router.patch("/runs/{run_id}")
def update_run_status(
    run_id: str,
    body: StatusIn,
    request: Request,
    x_actor: str | None = Header(default=None),
) -> dict[str, Any]:
    store = _store(request)
    current = store.get_run(run_id)
    if current is None:
        raise HTTPException(status_code=404, detail="run not found")
    target = body.status
    if target not in TRANSITIONS:
        raise HTTPException(status_code=422, detail=f"unknown status {target}")
    if target not in TRANSITIONS[current["status"]]:
        raise HTTPException(
            status_code=409,
            detail=f"illegal transition {current['status']} -> {target}",
        )
    row = store.set_run_status(run_id, target)
    _audit(store, _actor(x_actor), "run.status_changed", "run", run_id, {"from": current["status"], "to": target})
    return row


@router.get("/audit")
def list_audit(
    request: Request,
    entity_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    rows = _store(request).list_audit(entity_id, limit)
    return {"items": rows, "count": len(rows)}


@router.get("/metrics")
def metrics(request: Request) -> dict[str, Any]:
    store = _store(request)
    counts = store.counts()
    return {
        "uptime_seconds": round(time.time() - STARTED, 1),
        "runs_by_status": counts,
        "runs_total": sum(counts.values()),
        "audit_events_total": store.audit_count(),
        "store": type(store).__name__,
    }
