"""Agent specification wizard (Sprint 2).

Two properties matter more than the feature itself:
the draft is deterministic and stateless, and approval is the only path that
registers an agent — no Jira issue is created here (that is Sprint 3).
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

from app import specification as spec_generator  # noqa: E402

INTAKE = {
    "name": "Radar IA",
    "objective": "Produire chaque lundi une veille des sorties IA en Europe",
    "problem": "la veille est faite à la main et rate des publications",
    "outcomes": ["un rapport le lundi matin", "les sources sont toujours citées"],
    "constraints": ["aucune écriture dans Jira"],
    "skills": ["research", "reporting"],
    "tools": ["web"],
    "permissions": ["read"],
    "kpis": ["articles traités: 40 par semaine"],
}


@pytest.fixture()
def client():
    main = importlib.import_module("app.main")
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def test_generation_is_deterministic():
    assert spec_generator.draft(dict(INTAKE)) == spec_generator.draft(dict(INTAKE))


def test_backlog_has_one_story_per_skill():
    document = spec_generator.draft(dict(INTAKE))
    stories = document["backlog"]["stories"]
    assert [s["skill"] for s in stories] == ["research", "reporting"]
    assert [s["key"] for s in stories] == ["S1", "S2"]


def test_every_story_has_given_when_then_and_a_gate():
    document = spec_generator.draft(dict(INTAKE))
    for story in document["backlog"]["stories"]:
        assert story["acceptance"], story["key"]
        assert set(story["acceptance"][0]) == {"given", "when", "then"}
        assert any("approbation humaine" in c["then"] for c in story["acceptance"])
        assert story["tasks"]


def test_human_gates_are_in_the_workflow():
    document = spec_generator.draft(dict(INTAKE))
    gates = document["workflow"]["human_gates"]
    assert gates == ["spec_review", "backlog_approval", "prod_go_no_go"]
    assert [s["id"] for s in document["workflow"]["steps"] if s["gate"]] == gates


def test_kpi_target_is_not_invented():
    document = spec_generator.draft(dict(INTAKE))
    assert document["kpis"][0] == {
        "name": "articles traités",
        "target": "40 par semaine",
        "measure": "valeur mesurée à chaque exécution pour « articles traités », journalisée avec sa date",
    }
    unknown = spec_generator.draft({**INTAKE, "kpis": ["satisfaction"]})
    assert unknown["kpis"][0]["target"] == "à définir"


def test_unknown_skill_is_rejected():
    with pytest.raises(ValueError, match="unknown skill"):
        spec_generator.draft({**INTAKE, "skills": ["research", "telepathy"]})


def test_short_objective_is_rejected():
    with pytest.raises(ValueError, match="too short"):
        spec_generator.draft({**INTAKE, "objective": "faire mieux"})


def test_credentials_in_intake_are_rejected():
    with pytest.raises(ValueError, match="credentials"):
        spec_generator.draft({**INTAKE, "constraints": ["utiliser le token GitHub en dur"]})


def test_approve_permission_is_always_present():
    document = spec_generator.draft({**INTAKE, "permissions": ["write"]})
    assert "approve" in [p["id"] for p in document["permissions"]]


def test_draft_endpoint_stores_nothing(client):
    before = client.get("/api/v1/specifications").json()["count"]
    response = client.post("/api/v1/specifications/draft", json=INTAKE)
    assert response.status_code == 200
    assert response.json()["backlog"]["epic"]["title"].startswith("Radar IA")
    assert client.get("/api/v1/specifications").json()["count"] == before


def test_draft_rejects_unknown_skill_with_422(client):
    response = client.post("/api/v1/specifications/draft", json={**INTAKE, "skills": ["magie"]})
    assert response.status_code == 422
    assert "unknown skill" in response.json()["detail"]


def test_catalog_feeds_the_wizard(client):
    catalog = client.get("/api/v1/specifications/catalog").json()
    assert {s["id"] for s in catalog["skills"]} == set(spec_generator.SKILL_CATALOG)
    assert {"id": "jira", "label": "Jira Cloud (projet SCRUM, board 1)"} in catalog["tools"]


def test_create_then_approve_registers_the_agent(client):
    created = client.post("/api/v1/specifications", json=INTAKE, headers={"x-actor": "soufiane"})
    assert created.status_code == 201
    row = created.json()
    assert row["status"] == "draft"
    assert row["agent_id"] is None

    assert client.get("/api/v1/agents").json()["count"] == 0

    approved = client.post(f"/api/v1/specifications/{row['id']}/approve", headers={"x-actor": "soufiane"})
    assert approved.status_code == 200
    body = approved.json()
    assert body["status"] == "approved"
    assert body["approved_by"] == "soufiane"
    assert body["agent_id"]

    agents = client.get("/api/v1/agents").json()
    assert agents["count"] == 1
    assert agents["items"][0]["name"] == "radar-ia"


def test_approval_is_idempotent(client):
    row = client.post("/api/v1/specifications", json=INTAKE).json()
    first = client.post(f"/api/v1/specifications/{row['id']}/approve").json()
    second = client.post(f"/api/v1/specifications/{row['id']}/approve").json()
    assert first["agent_id"] == second["agent_id"]
    assert client.get("/api/v1/agents").json()["count"] == 1


def test_approval_is_audited(client):
    row = client.post("/api/v1/specifications", json=INTAKE, headers={"x-actor": "reviewer"}).json()
    client.post(f"/api/v1/specifications/{row['id']}/approve", headers={"x-actor": "reviewer"})
    events = client.get(f"/api/v1/audit?entity_id={row['id']}").json()["items"]
    actions = [e["action"] for e in events]
    assert "specification.created" in actions
    assert "specification.approved" in actions
    assert all(e["actor"] == "reviewer" for e in events)


def test_unknown_specification_is_404(client):
    assert client.post("/api/v1/specifications/nope/approve").status_code == 404
    assert client.get("/api/v1/specifications/nope").status_code == 404
