"""AI Agent Factory API.

SCRUM-19 established the liveness/readiness contract that the DEV, Qualité and
Production stages probe. SCRUM-22 adds the isolated PostgreSQL foundation,
SCRUM-32 the agents/runs API, SCRUM-31 the audit trail and SCRUM-41 the run
status tracking. Business routers live in ``api.py``; this module owns the app
lifecycle and the readiness contract.
"""
from __future__ import annotations

import os
import socket
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .api import router
from .api_jira import router as jira_router
from .api_prd import router as prd_router
from .api_workflow import router as workflow_router
from .api_eval import router as eval_router
from .api_plans import router as plans_router
from .api_release import router as release_router
from .api_obs import router as obs_router
from .store import build_store


def _tcp_check(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _dependencies() -> dict[str, dict[str, Any]]:
    db_host = os.getenv("FACTORY_DB_HOST", "db")
    db_port = int(os.getenv("FACTORY_DB_PORT", "5432"))
    cache_host = os.getenv("FACTORY_CACHE_HOST", "cache")
    cache_port = int(os.getenv("FACTORY_CACHE_PORT", "6379"))
    return {
        "database": {"host": db_host, "port": db_port, "ok": _tcp_check(db_host, db_port)},
        "cache": {"host": cache_host, "port": cache_port, "ok": _tcp_check(cache_host, cache_port)},
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = build_store()
    store.init()
    app.state.store = store
    yield
    store.close()


app = FastAPI(
    title="AI Agent Factory API",
    version="0.2.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)
app.include_router(router)
app.include_router(jira_router)
app.include_router(prd_router)
app.include_router(workflow_router)
app.include_router(eval_router)
app.include_router(plans_router)
app.include_router(release_router)
app.include_router(obs_router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness: the process is up. Never touches dependencies."""
    return {"status": "ok", "env": os.getenv("FACTORY_ENV", "dev")}


@app.get("/readyz")
def readyz() -> JSONResponse:
    """Readiness: every declared dependency accepts a TCP connection."""
    deps = _dependencies()
    ready = all(dep["ok"] for dep in deps.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not-ready", "dependencies": deps},
    )
