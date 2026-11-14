"""PRD endpoints (SCRUM-25, SCRUM-34, SCRUM-35)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import prd as prd_generator
from .redaction import redact

router = APIRouter(prefix="/api/v1/prd", tags=["prd"])


class PrdIn(BaseModel):
    request: str = Field(min_length=10, max_length=4000)
    source_key: str | None = Field(default=None, max_length=32, pattern=r"^[A-Z][A-Z0-9]+-\d+$")


class DecisionIn(BaseModel):
    decision: str = Field(min_length=1, max_length=200)


def _store(request: Request):
    return request.app.state.store


@router.post("", status_code=201)
def create(body: PrdIn, request: Request, x_actor: str | None = Header(default=None)) -> dict[str, Any]:
    store = _store(request)
    try:
        document = prd_generator.generate(body.request, body.source_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    row = store.create_prd(body.source_key, document)
    store.add_audit(
        (x_actor or "anonymous")[:120],
        "prd.created",
        "prd",
        row["id"],
        redact({"source_key": body.source_key, "title": document["title"]}),
    )
    return row


@router.get("")
def list_prd(request: Request, limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    rows = _store(request).list_prds(limit)
    return {"items": rows, "count": len(rows)}


@router.get("/{prd_id}")
def get_prd(prd_id: str, request: Request) -> dict[str, Any]:
    row = _store(request).get_prd(prd_id)
    if row is None:
        raise HTTPException(status_code=404, detail="prd not found")
    return row


@router.get("/{prd_id}/markdown")
def get_markdown(prd_id: str, request: Request):
    from fastapi.responses import PlainTextResponse

    row = _store(request).get_prd(prd_id)
    if row is None:
        raise HTTPException(status_code=404, detail="prd not found")
    return PlainTextResponse(prd_generator.render_markdown(row["payload"]))


@router.post("/{prd_id}/approve")
def approve(
    prd_id: str,
    request: Request,
    x_actor: str | None = Header(default=None),
) -> dict[str, Any]:
    """Human approval of a PRD. Approval is a state change, so it is audited."""
    store = _store(request)
    current = store.get_prd(prd_id)
    if current is None:
        raise HTTPException(status_code=404, detail="prd not found")
    if current["status"] == "approved":
        return current
    row = store.set_prd_status(prd_id, "approved")
    store.add_audit(
        (x_actor or "anonymous")[:120],
        "prd.approved",
        "prd",
        prd_id,
        redact({"from": current["status"], "to": "approved"}),
    )
    return row
