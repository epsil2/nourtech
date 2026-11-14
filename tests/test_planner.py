"""A story becomes a scoped, approved, PR-linked plan (SCRUM-27, SCRUM-37, SCRUM-38)."""
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

PR = "https://github.com/epsil2/nourtech/pull/42"


@pytest.fixture()
def client():
    main = importlib.import_module("app.main")
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def _plan(client, key="SCRUM-77", summary="Exposer un endpoint de lecture des plans", description="plan et endpoint pour la story"):
    return client.post(
        "/api/v1/plans",
        json={"issue_key": key, "summary": summary, "description": description},
        headers={"x-actor": "owner"},
    )


def test_a_plan_scopes_the_change_and_names_its_branch(client):
    r = _plan(client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "draft"
    payload = body["payload"]
    assert payload["branch"].startswith("feature/scrum-77-")
    assert "services/api/app/planner.py" in payload["scope"]
    assert set(payload["tests_required"]) == {"unit", "integration", "security", "end_to_end"}
    assert [s["index"] for s in payload["steps"]] == [1, 2, 3, 4, 5]


def test_a_story_without_a_recognisable_area_falls_back_to_the_default_scope(client):
    body = _plan(client, summary="Prendre en compte le retour des utilisateurs", description="").json()
    assert body["payload"]["scope"] == ["services/api/app/", "tests/"]


def test_a_malformed_issue_key_is_a_422(client):
    r = _plan(client, key="TICKET-1")
    assert r.status_code == 422
    assert "SCRUM-12" in r.json()["detail"]


def test_a_plan_cannot_carry_a_pull_request_before_approval(client):
    plan_id = _plan(client).json()["id"]
    r = client.post(f"/api/v1/plans/{plan_id}/link", json={"pull_request_url": PR})
    assert r.status_code == 409
    assert "approved" in r.json()["detail"]


def test_approve_then_link_is_the_only_order_that_works(client):
    plan_id = _plan(client).json()["id"]
    approve = client.post(f"/api/v1/plans/{plan_id}/approve", headers={"x-actor": "owner"})
    assert approve.status_code == 200
    assert approve.json()["status"] == "approved"
    assert approve.json()["approved_by"] == "owner"

    link = client.post(f"/api/v1/plans/{plan_id}/link", json={"pull_request_url": PR})
    assert link.status_code == 200
    assert link.json()["status"] == "linked"
    assert link.json()["pull_request_url"] == PR


def test_a_pull_request_outside_the_repository_is_refused(client):
    plan_id = _plan(client).json()["id"]
    client.post(f"/api/v1/plans/{plan_id}/approve")
    r = client.post(f"/api/v1/plans/{plan_id}/link", json={"pull_request_url": "https://gitlab.com/x/y/-/merge_requests/1"})
    assert r.status_code == 422


def test_approving_twice_is_a_409(client):
    plan_id = _plan(client).json()["id"]
    assert client.post(f"/api/v1/plans/{plan_id}/approve").status_code == 200
    second = client.post(f"/api/v1/plans/{plan_id}/approve")
    assert second.status_code == 409


def test_plans_are_listed_by_story_and_audited(client):
    _plan(client, key="SCRUM-78", summary="Un plan pour la story 78")
    items = client.get("/api/v1/plans", params={"issue_key": "SCRUM-78"}).json()["items"]
    assert len(items) == 1
    actions = [e["action"] for e in client.get("/api/v1/audit", params={"limit": 20}).json()["items"]]
    assert "plan.created" in actions


def test_an_unknown_plan_is_a_404(client):
    assert client.get("/api/v1/plans/00000000-0000-0000-0000-000000000000").status_code == 404
