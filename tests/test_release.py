"""Provenance, readiness, notes and rollback (SCRUM-47, SCRUM-48, SCRUM-49)."""
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

COMMIT = "e802dce51f3db857cc29eda40b572737ff56473d"
PREVIOUS = "38f0388123456789012345678901234567890123"
STORIES = [
    {"key": "SCRUM-47", "summary": "Un artefact porte son origine et son commit", "status": "Done", "type": "Story"},
    {"key": "SCRUM-48", "summary": "La prete a livrer se prouve, elle ne se declare pas", "status": "Done", "type": "Task"},
]


@pytest.fixture()
def client():
    main = importlib.import_module("app.main")
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def _artifact(client, commit=COMMIT, version="1.5.0", passed=107, failed=0, workflow="ci", source="github", stories=None):
    return client.post(
        "/api/v1/artifacts",
        json={
            "version": version,
            "commit_sha": commit,
            "workflow": workflow,
            "source": source,
            "tests": {"passed": passed, "failed": failed, "run_id": "43615000000"},
            "stories": stories if stories is not None else STORIES,
        },
        headers={"x-actor": "soufiane"},
    )


def _ready(client, commit=COMMIT, target="production", stories=None):
    return client.post(
        "/api/v1/releases/readiness",
        json={
            "commit_sha": commit,
            "target": target,
            "stories": stories if stories is not None else STORIES,
            "blockers": [],
        },
    )


def _plan(client, commit=COMMIT):
    return client.post(
        "/api/v1/releases/plan",
        json={"commit_sha": commit, "target": "production", "stories": STORIES, "blockers": []},
        headers={"x-actor": "soufiane"},
    )


def test_an_artifact_carries_its_commit_and_a_verifiable_signature(client):
    r = _artifact(client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["commit_sha"] == COMMIT
    assert body["signature"].startswith("sha256:")
    assert body["tests"]["passed"] == 107
    verified = client.get(f"/api/v1/artifacts/{COMMIT}/verify").json()
    assert verified["verified"] is True


def test_an_artifact_is_immutable(client):
    assert _artifact(client).status_code == 201
    again = _artifact(client, version="9.9.9")
    assert again.status_code == 201 or again.status_code == 409
    if again.status_code == 409:
        assert "immutable" in again.json()["detail"]


def test_a_mirror_or_untrusted_workflow_cannot_produce_an_artifact(client):
    assert _artifact(client, commit=PREVIOUS, source="gitlab").status_code == 422
    assert _artifact(client, commit=PREVIOUS, workflow="mirror").status_code == 422
    assert _artifact(client, commit=PREVIOUS, passed=0).status_code == 422
    assert _artifact(client, commit=PREVIOUS, passed=5, failed=1).status_code == 422


def test_readiness_reports_what_is_missing_and_never_approves(client):
    _artifact(client)
    body = _ready(client).json()
    assert body["ready"] is False
    assert "evidence" in body["missing"]  # no evaluation recorded for this commit
    assert body["approved"] is False
    assert body["risk"] == "high"


def test_readiness_needs_every_story_done_and_no_blocker(client):
    _artifact(client)
    client.post("/api/v1/evaluations", json={"subject": COMMIT, "dataset": "quality",
        "output": "## Livraison\n1. plan\n2. preuve\nAcceptance criteria: signe\n", "threshold": 0.8})
    open_story = [dict(STORIES[0], status="In Progress"), STORIES[1]]
    body = _ready(client, stories=open_story).json()
    assert body["ready"] is False and "stories" in body["missing"]
    blocked = client.post(
        "/api/v1/releases/readiness",
        json={"commit_sha": COMMIT, "target": "production", "stories": STORIES, "blockers": ["CVE non traitee"]},
    ).json()
    assert blocked["ready"] is False and "blockers" in blocked["missing"]
    good = _ready(client).json()
    assert good["ready"] is True and good["risk"] == "low" and good["approved"] is False


def test_a_release_needs_a_human_go_after_a_positive_report(client):
    _artifact(client)
    client.post("/api/v1/evaluations", json={"subject": COMMIT, "dataset": "quality",
        "output": "## Livraison\n1. plan\n2. preuve\nAcceptance criteria: signe\n", "threshold": 0.8})
    planned = _plan(client)
    assert planned.status_code == 200, planned.text
    rid = planned.json()["id"]
    assert planned.json()["status"] == "draft"
    assert client.post(f"/api/v1/releases/{rid}/notes", headers={"x-actor": "soufiane"}).status_code == 409
    assert client.post(f"/api/v1/releases/{rid}/publish").status_code == 409
    approved = client.post(f"/api/v1/releases/{rid}/approve", headers={"x-actor": "soufiane"})
    assert approved.status_code == 200 and approved.json()["approved_by"] == "soufiane"


def test_notes_are_derived_and_checked_against_the_artifact(client):
    _artifact(client)
    client.post("/api/v1/evaluations", json={"subject": COMMIT, "dataset": "quality",
        "output": "## Livraison\n1. plan\n2. preuve\nAcceptance criteria: signe\n", "threshold": 0.8})
    rid = _plan(client).json()["id"]
    client.post(f"/api/v1/releases/{rid}/approve", headers={"x-actor": "soufiane"})
    noted = client.post(f"/api/v1/releases/{rid}/notes", headers={"x-actor": "soufiane"})
    assert noted.status_code == 200, noted.text
    text = noted.json()["notes"]
    assert all(story["key"] in text for story in STORIES)
    assert COMMIT in text

    from app import release as rules

    with pytest.raises(ValueError):
        rules.verify_notes(text + "- SCRUM-99 — pas dans l artefact", {"stories": [s["key"] for s in STORIES]})


def test_publish_then_rollback_to_a_published_release(client):
    for commit, version in ((PREVIOUS, "1.4.0"), (COMMIT, "1.5.0")):
        _artifact(client, commit=commit, version=version)
        client.post("/api/v1/evaluations", json={"subject": commit, "dataset": "quality",
            "output": "## Livraison\n1. plan\n2. preuve\nAcceptance criteria: signe\n", "threshold": 0.8})
    old = _plan(client, PREVIOUS).json()["id"]
    client.post(f"/api/v1/releases/{old}/approve", headers={"x-actor": "soufiane"})
    client.post(f"/api/v1/releases/{old}/notes", headers={"x-actor": "soufiane"})
    assert client.post(f"/api/v1/releases/{old}/publish", headers={"x-actor": "soufiane"}).json()["status"] == "published"

    new = _plan(client).json()["id"]
    client.post(f"/api/v1/releases/{new}/approve", headers={"x-actor": "soufiane"})
    client.post(f"/api/v1/releases/{new}/notes", headers={"x-actor": "soufiane"})
    assert client.post(f"/api/v1/releases/{new}/publish", headers={"x-actor": "soufiane"}).json()["status"] == "published"

    refused = client.post(f"/api/v1/releases/{new}/rollback", json={"target_release_id": old, "reason": "regression en production", "schema_compatible": False})
    assert refused.status_code == 409 and "schema compatibility" in refused.json()["detail"]

    done = client.post(f"/api/v1/releases/{new}/rollback", json={"target_release_id": old, "reason": "regression en production", "schema_compatible": True}, headers={"x-actor": "soufiane"})
    assert done.status_code == 200, done.text
    assert done.json()["release"]["status"] == "rolled_back"
    assert done.json()["release"]["rolled_back_to"] == old


def test_a_draft_release_cannot_be_rolled_back(client):
    _artifact(client)
    client.post("/api/v1/evaluations", json={"subject": COMMIT, "dataset": "quality",
        "output": "## Livraison\n1. plan\n2. preuve\nAcceptance criteria: signe\n", "threshold": 0.8})
    rid = _plan(client).json()["id"]
    r = client.post(f"/api/v1/releases/{rid}/rollback", json={"target_release_id": PREVIOUS, "reason": "annulation", "schema_compatible": True})
    assert r.status_code == 409
