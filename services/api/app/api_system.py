"""Monitoring: real host numbers + real factory counters, plus the gates in force."""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Request

from . import jira_write, system

router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.get("")
def snapshot(request: Request) -> dict[str, Any]:
    store = request.app.state.store
    return {
        "host": system.host_snapshot(),
        "factory": system.factory_snapshot(store),
        "gates": {
            "human_approval_required": True,
            "jira_write_enabled": jira_write.JiraWriter().enabled,
            "deployment_requires_go_no_go": True,
        },
        "env": os.getenv("FACTORY_ENV", "dev"),
    }


@router.get("/events")
def events(request: Request, limit: int = 20) -> dict[str, Any]:
    """Last audit events, already redacted at write time."""
    rows = request.app.state.store.list_audit(None, max(1, min(limit, 100)))
    return {"items": rows, "count": len(rows)}
