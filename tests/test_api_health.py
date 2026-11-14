"""Unit tests for the Factory API health surface (foundation for SCRUM-22)."""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))
os.environ.setdefault("FACTORY_STORE", "memory")
factory_api = importlib.import_module("app.main")

# The lifespan builds the store; the health surface must not need it, so these
# tests drive the app without entering the context manager.
client = TestClient(factory_api.app)


def test_healthz_is_dependency_free():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readyz_reports_503_when_dependencies_are_down(monkeypatch):
    monkeypatch.setattr(factory_api, "_tcp_check", lambda host, port, timeout=2.0: False)
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["status"] == "not-ready"


def test_readyz_reports_200_when_dependencies_are_up(monkeypatch):
    monkeypatch.setattr(factory_api, "_tcp_check", lambda host, port, timeout=2.0: True)
    response = client.get("/readyz")
    assert response.status_code == 200
    assert set(response.json()["dependencies"]) == {"database", "cache"}


def test_docs_endpoints_are_disabled():
    assert client.get("/docs").status_code == 404
