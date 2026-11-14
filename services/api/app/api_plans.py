"""Plan endpoints (SCRUM-27, SCRUM-37, SCRUM-38).

Order matters and is enforced: a plan is built, a human approves it, and only then
can it carry a pull request. Skipping a step is a 409, never a silent success.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import planner

router = APIRouter(prefix="/api/v1/plans", tags=["plans"])


class PlanIn(BaseModel):
    issue_key: str = Field(min_length=6, max_length=12)
    summary: str = Field(min_length=5, max_length=200)
    description: str = Field(default="", max_length=8000)
    workflow_id: str | None = Field(default=None, max_length=64)


class LinkIn(BaseModel):
    pull_request_url: str = Field(max_length=300)


def _store(request: Request):
    return request.app.state.store


def _get(store, plan_id: str) -> dict[str, Any]:
    row = store.get_plan(plan_id)
    if row is None:
        raise HTTPException(status_code=404, detail="plan not found")
    return row


@router.post("", status_code=201)
def create_plan(body: PlanIn, request: Request, x_actor: str | None = Header(default=None)) -> dict[str, Any]:
    store = _store(request)
    try:
        payload = planner.build(body.issue_key, body.summary, body.description, body.workflow_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    row = store.create_plan(issue_key=payload["issue_key"], payload=payload, workflow_id=body.workflow_id)
    store.add_audit((x_actor or "anonymous")[:120], "plan.created", "plan", row["id"], {"issue_key": body.issue_key})
    return row


@router.get("")
def list_plans(request: Request, issue_key: str | None = Query(default=None), limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    rows = _store(request).list_plans(issue_key, limit)
    return {"items": rows, "count": len(rows)}


@router.get("/{plan_id}")
def get_plan(plan_id: str, request: Request) -> dict[str, Any]:
    return _get(_store(request), plan_id)


@router.post("/{plan_id}/approve")
def approve(plan_id: str, request: Request, x_actor: str | None = Header(default=None)) -> dict[str, Any]:
    store = _store(request)
    plan = _get(store, plan_id)
    if plan["status"] != "draft":
        raise HTTPException(status_code=409, detail=f"plan is {plan['status']!r}, not draft")
    actor = (x_actor or "anonymous")[:120]
    row = store.set_plan(plan_id, status="approved", actor=actor)
    store.add_audit(actor, "plan.approved", "plan", plan_id, {"branch": plan["payload"]["branch"]})
    return row


@router.post("/{plan_id}/link")
def link(plan_id: str, body: LinkIn, request: Request, x_actor: str | None = Header(default=None)) -> dict[str, Any]:
    store = _store(request)
    plan = _get(store, plan_id)
    if plan["status"] == "draft":
        raise HTTPException(status_code=409, detail="the plan must be approved before it can carry a pull request")
    try:
        url = planner.check_pr_url(body.pull_request_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    actor = (x_actor or "anonymous")[:120]
    row = store.set_plan(plan_id, status="linked", actor=actor, pull_request_url=url)
    store.add_audit(actor, "plan.linked", "plan", plan_id, {"pull_request_url": url})
    return row
