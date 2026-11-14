"""Business request entry point (chain of workflows).

``POST /api/v1/requests`` is the single call a business owner makes: it opens the
discovery workflow and returns the request id. From there the chain advances on
human decisions (see ``app.chain``). ``GET /api/v1/requests/{id}`` answers the only
question that matters in a review: where is this request now, and what is the next
action?
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from . import chain as chainer
from . import workflow as engine

router = APIRouter(prefix="/api/v1/requests", tags=["requests"])


class RequestIn(BaseModel):
    request: str = Field(min_length=20, max_length=4000)
    source_key: str | None = Field(default=None, max_length=60)
    # opt-in: create (or reuse) the Jira story for this request
    mirror_jira: bool = False


@router.post("", status_code=201)
def create(
    body: RequestIn, request: Request, x_actor: str | None = Header(default=None)
) -> dict[str, Any]:
    store = request.app.state.store
    actor = (x_actor or "anonymous")[:120]
    try:
        opened = chainer.start_request(
            store,
            body.request,
            source_key=body.source_key,
            actor=actor,
            mirror_jira=body.mirror_jira,
        )
    except chainer.ChainError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except engine.WorkflowDefinitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "request_id": opened["request_id"],
        "workflow_id": opened["workflow"]["id"],
        "stage": chainer.STAGE_DISCOVERY,
        "status": opened["workflow"]["status"],
        "waiting_for": opened["workflow"].get("current_step"),
        "jira": opened["chain"].get("jira"),
        "next": f"POST /api/v1/workflows/{opened['request_id']}/decision",
    }


@router.get("/{request_id}")
def get(request_id: str, request: Request) -> dict[str, Any]:
    store = request.app.state.store
    try:
        return chainer.summarize(store, request_id)
    except chainer.ChainError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
