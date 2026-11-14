"""Evaluation endpoints (SCRUM-28, SCRUM-39)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import evaluation
from .redaction import redact

router = APIRouter(prefix="/api/v1/evaluations", tags=["evaluations"])


class EvalIn(BaseModel):
    subject: str = Field(min_length=1, max_length=120)
    dataset: str = Field(min_length=1, max_length=40)
    output: str = Field(min_length=1, max_length=20000)
    threshold: float = Field(default=0.8, gt=0, le=1)


@router.get("/datasets")
def list_datasets() -> dict[str, Any]:
    return {"items": evaluation.datasets()}


@router.post("", status_code=201)
def run(body: EvalIn, request: Request, x_actor: str | None = Header(default=None)) -> dict[str, Any]:
    store = request.app.state.store
    try:
        result = evaluation.evaluate(body.output, body.dataset, body.threshold)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    row = store.create_evaluation(
        subject=body.subject,
        dataset=result["dataset"],
        score=result["score"],
        threshold=result["threshold"],
        passed=result["passed"],
        critical_failures=result["critical_failures"],
        results=result["results"],
    )
    store.add_audit(
        (x_actor or "anonymous")[:120],
        "evaluation.recorded",
        "evaluation",
        str(row["id"]),
        {"subject": body.subject, "score": result["score"], "passed": result["passed"]},
    )
    return row


@router.get("")
def list_evaluations(
    request: Request,
    subject: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    rows = request.app.state.store.list_evaluations(subject, limit)
    return {"items": rows, "count": len(rows)}


@router.get("/{evaluation_id}")
def get_evaluation(evaluation_id: int, request: Request) -> dict[str, Any]:
    row = request.app.state.store.get_evaluation(evaluation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="evaluation not found")
    return row


@router.post("/{evaluation_id}/review")
def human_review(
    evaluation_id: int,
    body: dict[str, Any],
    request: Request,
    x_actor: str | None = Header(default=None),
) -> dict[str, Any]:
    """Record a human verdict on a scored evaluation (SCRUM-39)."""
    store = request.app.state.store
    if store.get_evaluation(evaluation_id) is None:
        raise HTTPException(status_code=404, detail="evaluation not found")
    decision = str(body.get("outcome", "")).lower()
    if decision not in ("accepted", "rejected"):
        raise HTTPException(status_code=422, detail="outcome must be 'accepted' or 'rejected'")
    note = redact(str(body.get("note", "")))[:500]
    row = store.review_evaluation(evaluation_id, decision=decision, actor=(x_actor or "anonymous")[:120], note=note)
    store.add_audit((x_actor or "anonymous")[:120], f"evaluation.{decision}", "evaluation", str(evaluation_id), {"note": note})
    return row
