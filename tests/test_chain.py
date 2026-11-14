"""Business request chain contract (SCRUM-54).

A request must reach the end of the chain without anyone copying payloads between
runs, and must stop at every human gate on the way. These tests pin both halves:
what the chain does by itself, and what it still refuses to do without a decision
or without quality evidence.
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

REQUEST = "Publier un radar IA hebdomadaire sur nourredine.tech avec 10 signaux verifies"
GOOD_OUTPUT = "## Livraison\n1. plan\n2. preuve\nAcceptance criteria: signe\n"


@pytest.fixture()
def client():
    main = importlib.import_module("app.main")
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def _open(client, **extra):
    body = {"request": REQUEST, **extra}
    r = client.post("/api/v1/requests", json=body, headers={"x-actor": "owner"})
    assert r.status_code == 201, r.text
    return r.json()


def _decide(client, workflow_id, decision="approved"):
    return client.post(
        f"/api/v1/workflows/{workflow_id}/decision",
        json={"decision": decision, "note": "ok"},
        headers={"x-actor": "owner"},
    )


def _workflows(client):
    rows = client.get("/api/v1/workflows?limit=200").json()["items"]
    return rows


def test_request_opens_discovery_and_stops_at_the_review(client):
    opened = _open(client)
    assert opened["request_id"] == opened["workflow_id"]
    assert opened["stage"] == "discovery"
    assert opened["status"] == "awaiting_approval"
    assert opened["waiting_for"] == "architecture_review"

    summary = client.get(f"/api/v1/requests/{opened['request_id']}").json()
    assert summary["stage"] == "discovery"
    assert summary["request"] == REQUEST
    assert [s["stage"] for s in summary["stages"]] == ["discovery"]
    assert summary["stages"][0]["waiting_for"] == "architecture_review"


def test_request_rejects_a_too_short_demand(client):
    r = client.post("/api/v1/requests", json={"request": "trop court"}, headers={"x-actor": "owner"})
    assert r.status_code == 422


def test_one_approval_starts_delivery_which_waits_for_its_own_gate(client):
    opened = _open(client)
    decided = _decide(client, opened["request_id"])
    assert decided.status_code == 200, decided.text
    body = decided.json()
    assert body["status"] == "completed"
    assert body["chain"]["action"] == "delivery_started"

    delivery_id = body["chain"]["delivery_workflow_id"]
    assert body["chain"]["waiting_for"] == "implement"

    delivery = client.get(f"/api/v1/workflows/{delivery_id}").json()
    assert delivery["definition"] == "delivery"
    assert delivery["status"] == "awaiting_approval"
    # the backlog is inherited, nobody copied it
    assert delivery["state"]["backlog"]["items"]
    assert delivery["state"]["plan"]["items"]

    summary = client.get(f"/api/v1/requests/{opened['request_id']}").json()
    assert [s["stage"] for s in summary["stages"]] == ["discovery", "delivery"]
    assert summary["stages"][1]["workflow_id"] == delivery_id
    assert summary["stages"][1]["waiting_for"] == "implement"
    assert summary["stage"] == "delivery"


def test_a_replayed_decision_never_opens_a_second_delivery(client):
    opened = _open(client)
    _decide(client, opened["request_id"])
    again = _decide(client, opened["request_id"])
    assert again.status_code == 409
    deliveries = [w for w in _workflows(client) if w["definition"] == "delivery"]
    assert len(deliveries) == 1


def test_release_gate_refuses_without_recorded_evaluation(client):
    opened = _open(client)
    delivery_id = _decide(client, opened["request_id"]).json()["chain"]["delivery_workflow_id"]

    implemented = _decide(client, delivery_id)
    assert implemented.status_code == 200, implemented.text
    assert implemented.json()["current_step"] == "release_ready"

    refused = _decide(client, delivery_id)
    assert refused.status_code == 409
    assert "no evaluation recorded" in refused.json()["detail"]


def test_accepted_evaluation_completes_delivery_and_prepares_release(client):
    opened = _open(client)
    delivery_id = _decide(client, opened["request_id"]).json()["chain"]["delivery_workflow_id"]
    _decide(client, delivery_id)

    evaluation = client.post(
        "/api/v1/evaluations",
        json={"subject": delivery_id, "dataset": "quality", "output": GOOD_OUTPUT, "threshold": 0.8},
        headers={"x-actor": "owner"},
    )
    assert evaluation.status_code == 201, evaluation.text
    evaluation_id = evaluation.json()["id"]
    reviewed = client.post(
        f"/api/v1/evaluations/{evaluation_id}/review",
        json={"outcome": "accepted", "note": "preuve QA"},
        headers={"x-actor": "owner"},
    )
    assert reviewed.status_code == 200, reviewed.text

    last = _decide(client, delivery_id)
    assert last.status_code == 200, last.text
    assert last.json()["status"] == "completed"
    assert last.json()["chain"]["action"] == "release_prepared"

    summary = client.get(f"/api/v1/requests/{opened['request_id']}").json()
    assert [s["stage"] for s in summary["stages"]] == ["discovery", "delivery", "release"]
    assert summary["release"]["ready"] is True
    assert summary["release"]["qa"]["passed"] is True
    assert summary["release"]["stories"], "les stories de release doivent etre preparees"
    assert "artifact" in " ".join(summary["release"]["requires"])
    assert summary["stage"] == "release"
    # a delivery stage that is done points at the release chain, not at another decision
    assert "artifacts" in summary["stages"][2]["next"]

    actions = {row["action"] for row in summary["audit"]}
    assert {"request.opened", "chain.delivery.started", "chain.release.prepared"} <= actions


def test_a_manually_started_workflow_is_not_chained(client):
    started = client.post(
        "/api/v1/workflows",
        json={"definition": "discovery", "input": {"request": REQUEST}},
        headers={"x-actor": "owner"},
    )
    assert started.status_code == 201
    workflow_id = started.json()["id"]

    decided = _decide(client, workflow_id)
    assert decided.status_code == 200
    assert "chain" not in decided.json()
    assert [w for w in _workflows(client) if w["definition"] == "delivery"] == []


def test_mirroring_jira_off_the_write_path_does_not_block_the_request(client):
    opened = _open(client, mirror_jira=True, source_key="SCRUM-55")
    jira = opened["jira"]
    assert jira["mirrored"] is False
    assert jira["reason"]
    summary = client.get(f"/api/v1/requests/{opened['request_id']}").json()
    assert summary["jira"]["mirrored"] is False
    assert summary["source_key"] == "SCRUM-55"


def test_unknown_request_is_a_404(client):
    assert client.get("/api/v1/requests/does-not-exist").status_code == 404
    # a delivery workflow id is not a request id
    opened = _open(client)
    delivery_id = _decide(client, opened["request_id"]).json()["chain"]["delivery_workflow_id"]
    assert client.get(f"/api/v1/requests/{delivery_id}").status_code == 404
