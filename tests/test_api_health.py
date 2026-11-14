"""Unit tests for the Factory API health surface (foundation for SCRUM-22)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("factory_api", ROOT / "services" / "api" / "app" / "main.py")
factory_api = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(factory_api)


def test_healthz_is_dependency_free():
    client = TestClient(factory_api.app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readyz_reports_503_when_dependencies_are_down(monkeypatch):
    monkeypatch.setattr(factory_api, "_tcp_check", lambda host, port, timeout=2.0: False)
    client = TestClient(factory_api.app)
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["status"] == "not-ready"


def test_readyz_reports_200_when_dependencies_are_up(monkeypatch):
    monkeypatch.setattr(factory_api, "_tcp_check", lambda host, port, timeout=2.0: True)
    client = TestClient(factory_api.app)
    response = client.get("/readyz")
    assert response.status_code == 200
    assert set(response.json()["dependencies"]) == {"database", "cache"}


def test_docs_endpoints_are_disabled():
    client = TestClient(factory_api.app)
    assert client.get("/docs").status_code == 404
