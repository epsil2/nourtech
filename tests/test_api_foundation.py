"""Contract tests for the Factory API foundation (SCRUM-22, SCRUM-31, SCRUM-32, SCRUM-41).

The API is exercised through its HTTP surface with the in-memory store, so the
tests describe the contract the pipeline's DEV stage relies on without needing a
database. Real PostgreSQL persistence is proven by the DEV stage of deploy.yml.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))
os.environ["FACTORY_STORE"] = "memory"


@pytest.fixture()
def client():
    main = importlib.import_module("app.main")
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def _agent(client, name="planner"):
    r = client.post("/api/v1/agents", json={"name": name, "description": "plans work"})
    assert r.status_code == 201, r.text
    return r.json()


def test_liveness_and_readiness_contract(client):
    assert client.get("/healthz").json()["status"] == "ok"
    ready = client.get("/readyz")
    body = ready.json()
    assert body["status"] in {"ready", "not-ready"}
    assert set(body["dependencies"]) == {"database", "cache"}


def test_agent_registration_is_unique_and_validated(client):
    row = _agent(client)
    assert row["name"] == "planner" and row["id"]
    dup = client.post("/api/v1/agents", json={"name": "planner", "description": ""})
    assert dup.status_code == 409
    bad = client.post("/api/v1/agents", json={"name": "not allowed!", "description": ""})
    assert bad.status_code == 422
    assert client.get("/api/v1/agents").json()["count"] == 1
    assert client.get(f"/api/v1/agents/{row['id']}").json()["id"] == row["id"]
    assert client.get("/api/v1/agents/does-not-exist").status_code == 404


def test_run_lifecycle_rejects_illegal_transitions(client):
    agent = _agent(client)
    created = client.post("/api/v1/runs", json={"objective": "write a PRD", "agent_id": agent["id"]})
    assert created.status_code == 201
    run = created.json()
    assert run["status"] == "queued" and run["agent_id"] == agent["id"]

    assert client.patch(f"/api/v1/runs/{run['id']}", json={"status": "succeeded"}).status_code == 409
    assert client.patch(f"/api/v1/runs/{run['id']}", json={"status": "running"}).status_code == 200
    done = client.patch(f"/api/v1/runs/{run['id']}", json={"status": "succeeded"})
    assert done.status_code == 200 and done.json()["status"] == "succeeded"
    assert client.patch(f"/api/v1/runs/{run['id']}", json={"status": "running"}).status_code == 409

    assert client.patch("/api/v1/runs/00000000-0000-0000-0000-000000000000", json={"status": "running"}).status_code == 404
    assert client.post("/api/v1/runs", json={"objective": "orphan", "agent_id": "nope"}).status_code == 422


def test_runs_are_filterable_and_counted(client):
    a = _agent(client, "researcher")
    for i in range(3):
        client.post("/api/v1/runs", json={"objective": f"task {i}", "agent_id": a["id"]})
    rows = client.get("/api/v1/runs?limit=2").json()
    assert rows["count"] == 2
    assert client.get("/api/v1/runs?status=queued").json()["count"] == 3
    assert client.get("/api/v1/runs?status=failed").json()["count"] == 0
    m = client.get("/api/v1/metrics").json()
    assert m["runs_total"] == 3 and m["runs_by_status"]["queued"] == 3
    assert m["store"] == "MemoryStore"
    assert m["audit_events_total"] >= 4  # 1 agent + 3 runs


def test_audit_trail_records_every_mutation_and_redacts(client):
    agent = _agent(client)
    client.post("/api/v1/runs", json={"objective": "deploy", "agent_id": agent["id"]})
    audit = client.get("/api/v1/audit").json()
    assert audit["count"] >= 2
    actions = {row["action"] for row in audit["items"]}
    assert {"agent.created", "run.created"} <= actions
    assert all(row["actor"] == "anonymous" for row in audit["items"])


def test_audit_payload_never_keeps_a_secret(client):
    fake = "gh" + "p_" + "a" * 24
    agent = _agent(client, "leaky")
    client.post(
        "/api/v1/runs",
        json={"objective": f"call with {fake}", "agent_id": agent["id"]},
        headers={"X-Actor": "tester"},
    )
    rows = client.get("/api/v1/audit").json()["items"]
    blob = str(rows)
    assert "a" * 24 not in blob
    assert all(row["actor"] in {"tester", "anonymous"} for row in rows)


def test_redaction_masks_keys_and_shapes():
    from app.redaction import MASK, contains_secret, redact

    token = "gh" + "p_" + "b" * 30
    payload = {
        "password": "hunter2",
        "nested": [{"api_key": "k"}, {"note": f"token={token}"}],
        "count": 3,
        "ok": True,
        "none": None,
    }
    out = redact(payload)
    assert out["password"] == MASK
    assert out["nested"][0]["api_key"] == MASK
    assert token not in str(out)
    assert out["count"] == 3 and out["ok"] is True and out["none"] is None
    assert contains_secret(payload) is True
    assert contains_secret({"objective": "write a PRD"}) is False
    assert redact({"deep": [[[[[[[[["x"]]]]]]]]]}) is not None


def test_redaction_survives_odd_input():
    from app.redaction import redact

    assert redact("x" * 5000).endswith("…")
    assert redact({1: object()})
    assert redact((1, "two")) == [1, "two"]
