"""Factory API foundation: liveness and readiness only (scope of SCRUM-19).

Business endpoints, persistence models and auth belong to SCRUM-22. This module
exists so the isolated DEV stack has a real readiness signal to test.
"""
from __future__ import annotations

import os
import socket
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="AI Agent Factory API", version="0.1.0", docs_url=None, redoc_url=None)


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
