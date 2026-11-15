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
from .api_requests import router as requests_router
from .api_workflow import router as workflow_router
from .api_eval import router as eval_router
from .api_plans import router as plans_router
from .api_release import router as release_router
from .api_specs import router as specs_router
from .api_jira_board import router as jira_board_router
from .api_github import router as github_router
from .api_system import router as system_router
from .api_integrations import router as integrations_router
from .api_obs import router as obs_router
from .api_projects import boot as boot_projects, router as projects_router
from .api_executions import boot as boot_executions, router as executions_router
from .api_deployments import router as deployments_router
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
    boot_projects(store)
    boot_executions(store)
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
app.include_router(requests_router)
app.include_router(workflow_router)
app.include_router(eval_router)
app.include_router(plans_router)
app.include_router(release_router)
app.include_router(specs_router)
app.include_router(obs_router)
app.include_router(jira_board_router)
app.include_router(github_router)
app.include_router(system_router)
app.include_router(integrations_router)
app.include_router(projects_router)
app.include_router(executions_router)
app.include_router(deployments_router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness: the process is up. Never touches dependencies."""
    return {
        "status": "ok",
        "env": os.getenv("FACTORY_ENV", "dev"),
        # Name of the store implementation, not a probe: liveness must stay dependency-free.
        "store": "pg" if os.getenv("FACTORY_STORE", "pg").lower().startswith("pg") else "memory",
    }


@app.get("/readyz")
def readyz() -> JSONResponse:
    """Readiness: every declared dependency accepts a TCP connection."""
    deps = _dependencies()
    ready = all(dep["ok"] for dep in deps.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not-ready", "dependencies": deps},
    )
