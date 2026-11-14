"""Checkpointed workflow contract (SCRUM-23, SCRUM-33).

The behaviour under test is the one that matters for the human gate: a workflow
stops *before* an irreversible node, persists what it had computed, and only a
recorded human decision moves it forward — to the next interrupt or to the end.
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

REQUEST = "Publier une release hebdomadaire de la factory avec preuve de deploiement"


@pytest.fixture()
def client():
    main = importlib.import_module("app.main")
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def _start(client, definition="discovery", payload=None, actor="owner"):
    r = client.post(
        "/api/v1/workflows",
        json={"definition": definition, "input": payload or {"request": REQUEST}},
        headers={"x-actor": actor},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _decide(client, workflow_id, decision, note="", actor="owner"):
    return client.post(
        f"/api/v1/workflows/{workflow_id}/decision",
        json={"decision": decision, "note": note},
        headers={"x-actor": actor},
    )


def test_definitions_declare_their_human_gates(client):
    body = client.get("/api/v1/workflows/definitions").json()
    names = {item["name"] for item in body["items"]}
    assert {"discovery", "delivery"} <= names
    discovery = next(item for item in body["items"] if item["name"] == "discovery")
    gates = [node["name"] for node in discovery["nodes"] if node["approval"]]
    assert gates == ["architecture_review"]


def test_discovery_stops_before_the_architecture_review(client):
    wf = _start(client)
    assert wf["status"] == "awaiting_approval"
    assert wf["current_step"] == "architecture_review"
    assert wf["waiting_for"] == "architecture_review"
    # the PRD was already produced: stopping must not throw work away
    assert wf["state"]["prd"]["slug"]
    cps = client.get(f"/api/v1/workflows/{wf['id']}/checkpoints").json()["items"]
    assert [c["step"] for c in cps] == ["intake", "prd_draft", "architecture_review"]
    assert [c["seq"] for c in cps] == [1, 2, 3]
    assert cps[-1]["kind"] == "interrupt"
    assert cps[-1]["decision"] is None


def test_approval_resumes_from_the_checkpoint_and_completes(client):
    wf = _start(client)
    r = _decide(client, wf["id"], "approved", note="architecture validée")
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["status"] == "completed"
    assert done["current_step"] == "done"
    assert done["state"]["backlog"]["count"] == len(
        done["state"]["prd"]["sections"]["critères_d_acceptation"]
    )
    cps = client.get(f"/api/v1/workflows/{wf['id']}/checkpoints").json()["items"]
    assert cps[-1]["step"] == "backlog_plan"
    decision = [c for c in cps if c["kind"] == "decision"][0]
    assert decision["decision"] == "approved"
    assert decision["actor"] == "owner"
    assert decision["note"] == "architecture validée"


def test_rejection_cancels_and_never_runs_the_next_node(client):
    wf = _start(client)
    r = _decide(client, wf["id"], "rejected", note="hors périmètre")
    assert r.status_code == 200
    cancelled = r.json()
    assert cancelled["status"] == "cancelled"
    assert "backlog" not in cancelled["state"]
    cps = client.get(f"/api/v1/workflows/{wf['id']}/checkpoints").json()["items"]
    assert [c["kind"] for c in cps] == ["node", "node", "interrupt", "decision"]
    assert cps[-1]["decision"] == "rejected"


def test_a_second_decision_is_a_conflict(client):
    wf = _start(client)
    assert _decide(client, wf["id"], "approved").status_code == 200
    second = _decide(client, wf["id"], "approved")
    assert second.status_code == 409
    assert "awaiting_approval" in second.json()["detail"]


def test_decision_on_an_unknown_workflow_is_a_404(client):
    assert _decide(client, "00000000-0000-0000-0000-000000000000", "approved").status_code == 404


def test_unknown_definition_is_a_422(client):
    r = client.post("/api/v1/workflows", json={"definition": "nope", "input": {"request": REQUEST}})
    assert r.status_code == 422
    assert "unknown definition" in r.json()["detail"]


def test_request_too_short_is_a_422_not_a_crash(client):
    r = client.post("/api/v1/workflows", json={"definition": "discovery", "input": {"request": "trop court"}})
    assert r.status_code == 422


def test_delivery_has_two_gates_in_order(client):
    backlog = {"items": [{"index": 1, "acceptance": "une release signée", "status": "planned"}]}
    wf = _start(client, "delivery", {"backlog": backlog})
    assert wf["current_step"] == "implement"

    first = _decide(client, wf["id"], "approved", note="build")
    assert first.status_code == 200
    assert first.json()["current_step"] == "release_ready"
    assert first.json()["state"]["qa"]["passed"] is True

    second = _decide(client, wf["id"], "approved", note="go")
    assert second.status_code == 200
    assert second.json()["status"] == "completed"
    assert second.json()["state"]["release"]["ready"] is True


def test_delivery_without_a_backlog_is_refused(client):
    r = client.post("/api/v1/workflows", json={"definition": "delivery", "input": {}})
    assert r.status_code == 422


def test_every_move_lands_in_the_audit_trail(client):
    wf = _start(client)
    _decide(client, wf["id"], "approved")
    events = client.get("/api/v1/audit", params={"limit": 50}).json()["items"]
    actions = [e["action"] for e in events]
    assert "workflow.started" in actions
    assert "workflow.interrupted" in actions
    assert "workflow.approved" in actions
    assert "workflow.completed" in actions


def test_checkpoints_keep_the_state_each_step_saw(client):
    wf = _start(client)
    _decide(client, wf["id"], "approved")
    cps = client.get(f"/api/v1/workflows/{wf['id']}/checkpoints").json()["items"]
    by_step = {c["step"]: c for c in cps}
    assert "backlog" not in by_step["prd_draft"]["state"]
    assert by_step["backlog_plan"]["state"]["backlog"]["count"] >= 1
