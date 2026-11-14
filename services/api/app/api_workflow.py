"""Workflow endpoints (SCRUM-23, SCRUM-33).

A workflow is created, runs to its first interrupt, and then waits. Only a human
decision moves it: ``approved`` resumes from the checkpoint, ``rejected`` cancels
it. Everything is audited, and every checkpoint is readable afterwards so the
trail shows what each step saw.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import workflow as engine
from .redaction import redact

router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])


class StartIn(BaseModel):
    definition: str = Field(min_length=1, max_length=40)
    input: dict[str, Any] = Field(default_factory=dict)


class DecisionIn(BaseModel):
    decision: str = Field(pattern=r"^(approved|rejected)$")
    note: str = Field(default="", max_length=500)


def _store(request: Request):
    return request.app.state.store


def _get(store, workflow_id: str) -> dict[str, Any]:
    row = store.get_workflow(workflow_id)
    if row is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    return row


@router.get("/definitions")
def definitions() -> dict[str, Any]:
    return {
        "items": [
            {
                "name": name,
                "nodes": [
                    {"name": node.name, "approval": node.approval, "label": node.label}
                    for node in engine.DEFINITIONS[name]
                ],
            }
            for name in engine.definition_names()
        ]
    }


@router.post("", status_code=201)
def start(body: StartIn, request: Request, x_actor: str | None = Header(default=None)) -> dict[str, Any]:
    store = _store(request)
    try:
        return engine.start(store, body.definition, body.input, actor=(x_actor or "anonymous")[:120])
    except engine.WorkflowDefinitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("")
def list_workflows(
    request: Request,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    rows = _store(request).list_workflows(status, limit)
    return {"items": rows, "count": len(rows)}


@router.get("/{workflow_id}")
def get_workflow(workflow_id: str, request: Request) -> dict[str, Any]:
    return _get(_store(request), workflow_id)


@router.get("/{workflow_id}/checkpoints")
def checkpoints(workflow_id: str, request: Request) -> dict[str, Any]:
    store = _store(request)
    _get(store, workflow_id)
    rows = store.list_checkpoints(workflow_id)
    return {"items": rows, "count": len(rows)}


@router.post("/{workflow_id}/decision")
def decide(
    workflow_id: str,
    body: DecisionIn,
    request: Request,
    x_actor: str | None = Header(default=None),
) -> dict[str, Any]:
    store = _store(request)
    current = _get(store, workflow_id)
    actor = (x_actor or "anonymous")[:120]
    try:
        return engine.decide(
            store, current, decision=body.decision, actor=actor, note=redact(body.note)
        )
    except engine.WorkflowStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
