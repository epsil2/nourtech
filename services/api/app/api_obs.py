"""Observability endpoints (SCRUM-42).

Two questions are answered here: what is the process actually consuming, and what
happened to a given run. Both read recorded state, so a report can be checked
after the fact instead of being taken on trust.
"""
from __future__ import annotations

import os
import shutil
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(prefix="/api/v1", tags=["observability"])

STARTED = time.time()


def _meminfo() -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                if key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
                    out[key] = int(rest.strip().split()[0]) // 1024  # MiB
    except OSError:
        pass
    return out


def _disk(path: str = "/") -> dict[str, Any]:
    usage = shutil.disk_usage(path)
    return {
        "path": path,
        "total_mib": usage.total // (1024 * 1024),
        "used_mib": usage.used // (1024 * 1024),
        "used_pct": round(usage.used * 100.0 / usage.total, 1) if usage.total else 0.0,
    }


@router.get("/observability/resources")
def resources(request: Request) -> dict[str, Any]:
    store = request.app.state.store
    mem = _meminfo()
    return {
        "uptime_s": round(time.time() - STARTED, 1),
        "cpu_count": os.cpu_count() or 1,
        "memory_mib": mem,
        "disk": _disk("/"),
        "runs": store.counts(),
        "audit_events": store.audit_count(),
        "agents": len(store.list_agents()) if hasattr(store, "list_agents") else None,
        "releases": len(store.list_releases(None, 200)),
        "artifacts": len(store.list_artifacts(200)),
    }


@router.get("/runs/{run_id}/timeline")
def timeline(run_id: str, request: Request, limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    store = request.app.state.store
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    events = [
        {"action": e.get("action"), "actor": e.get("actor"), "at": e.get("at"), "payload": e.get("payload", {})}
        for e in store.list_audit(run_id, limit)
    ]
    return {"run": run, "events": events, "count": len(events)}


@router.get("/observability/audit")
def recent_audit(request: Request, entity_id: str | None = Query(default=None), limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    store = request.app.state.store
    rows = store.list_audit(entity_id, limit)
    return {"items": rows, "count": len(rows)}
