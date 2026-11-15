"""Identity, sessions and the guard in front of every business route.

These tests run with the guard *enabled*: they are the contract that a browser
without a session cannot create a project, cannot launch an execution and cannot
name itself in the audit trail.
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

ADMIN_PASSWORD = "correct-horse-battery"


@pytest.fixture()
def app_module(monkeypatch):
    monkeypatch.setenv("FACTORY_STORE", "memory")
    monkeypatch.setenv("FACTORY_AUTH", "on")
    monkeypatch.setenv("CAMILIA_BOOTSTRAP_USER", "soufiane")
    monkeypatch.setenv("CAMILIA_BOOTSTRAP_PASSWORD", ADMIN_PASSWORD)
    import app.security as security

    security.reset_throttle()
    main = importlib.import_module("app.main")
    importlib.reload(main)
    return main


@pytest.fixture()
def client(app_module):
    with TestClient(app_module.app) as c:
        yield c


def login(client, username="soufiane", password=ADMIN_PASSWORD):
    return client.post("/api/v1/auth/login", json={"username": username, "password": password})


# --------------------------------------------------------------------------- #
# passwords
# --------------------------------------------------------------------------- #
def test_password_hash_is_salted_and_verifiable():
    from app.security import hash_password, verify_password

    first = hash_password("s3cret-passphrase")
    second = hash_password("s3cret-passphrase")
    assert first != second, "two hashes of the same password must differ (salted)"
    assert "s3cret-passphrase" not in first
    assert verify_password("s3cret-passphrase", first)
    assert not verify_password("wrong", first)
    assert not verify_password("", "")


def test_password_minimum_length_is_enforced():
    from app.security import hash_password

    with pytest.raises(ValueError):
        hash_password("short")


# --------------------------------------------------------------------------- #
# login
# --------------------------------------------------------------------------- #
def test_bootstrap_admin_can_log_in_and_is_identified(client):
    response = login(client)
    assert response.status_code == 200
    assert response.json()["user"]["username"] == "soufiane"
    assert response.json()["user"]["role"] == "admin"
    assert "password" not in str(response.json()).lower()

    cookie = response.headers.get("set-cookie", "")
    assert "cam_session=" in cookie
    assert "HttpOnly" in cookie

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["role"] == "admin"


def test_wrong_password_is_rejected_and_audited(client, app_module):
    assert login(client, password="nope-nope-nope").status_code == 401
    rows = app_module.app.state.store.cp_list("user", limit=5)
    assert rows, "the bootstrap user must exist"
    assert "pbkdf2_sha256$" in rows[0]["password_hash"]
    assert "nope-nope-nope" not in str(rows[0])


def test_repeated_failures_are_throttled(client):
    for _ in range(5):
        assert login(client, password="wrong-password").status_code == 401
    assert login(client, password=ADMIN_PASSWORD).status_code == 429


def test_logout_revokes_the_session(client):
    assert login(client).status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 200
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401


# --------------------------------------------------------------------------- #
# the guard
# --------------------------------------------------------------------------- #
def test_projects_are_closed_without_a_session(client):
    assert client.get("/api/v1/projects").status_code == 401
    assert client.post("/api/v1/projects", json={"key": "ABC", "name": "x"}).status_code == 401


def test_executions_are_closed_without_a_session(client):
    assert client.get("/api/v1/executions").status_code == 401
    assert client.post("/api/v1/executions", json={}).status_code == 401


def test_health_stays_public(client):
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/v1/health").status_code == 200


def test_logged_in_user_can_reach_projects(client):
    assert login(client).status_code == 200
    assert client.get("/api/v1/projects").status_code == 200


# --------------------------------------------------------------------------- #
# no header-based impersonation
# --------------------------------------------------------------------------- #
def test_x_actor_header_cannot_impersonate(client, app_module):
    assert login(client).status_code == 200
    created = client.post(
        "/api/v1/projects",
        json={
            "key": "IMPERSON",
            "name": "Impersonation probe",
            "business_request": "prove the header is ignored",
            "jira_project": "SCRUM",
        },
        headers={"x-actor": "root"},
    )
    assert created.status_code in (201, 409, 422)
    events = app_module.app.state.store.list_audit(limit=50)
    actors = {row.get("actor") for row in events}
    assert "root" not in actors
    assert "soufiane" in actors


def test_roles_are_enforced_server_side(client, app_module):
    create_user_client = TestClient(app_module.app)
    with create_user_client as other:
        from app.security import create_user as mk_user

        mk_user(other.app.state.store, "reader", "read-only-pass", role="viewer")
        assert login(other, "reader", "read-only-pass").status_code == 200
        # a viewer may read
        assert other.get("/api/v1/projects").status_code == 200
        # but may not manage users
        assert other.get("/api/v1/auth/users").status_code == 403


# --------------------------------------------------------------------------- #
# Project Factory is closed too
# --------------------------------------------------------------------------- #
def test_factory_is_closed_without_a_session(client):
    assert client.get("/api/v1/factory/projects").status_code == 401
    assert client.get("/api/v1/factory/catalogue").status_code == 401
    assert client.post("/api/v1/factory/projects", json={"key": "NOPE1", "name": "x"}).status_code == 401
    assert client.post("/api/v1/factory/projects/abc/provision").status_code == 401


def test_viewer_cannot_provision_but_can_read(client, app_module):
    with TestClient(app_module.app) as other:
        from app.security import create_user as mk_user

        mk_user(other.app.state.store, "auditor", "auditor-pass", role="viewer")
        assert login(other, "auditor", "auditor-pass").status_code == 200
        assert other.get("/api/v1/factory/projects").status_code == 200
        refused = other.post(
            "/api/v1/factory/projects",
            json={"key": "VW01", "name": "viewer attempt", "template": "fastapi-api"},
        )
        assert refused.status_code == 403


def test_factory_actor_is_the_session_identity(client, app_module):
    assert login(client).status_code == 200
    created = client.post(
        "/api/v1/factory/projects",
        json={"key": "ACTOR1", "name": "Actor probe", "template": "fastapi-api"},
        headers={"x-actor": "root"},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["project"]["project"]["id"]
    rows = client.get(f"/api/v1/factory/projects/{project_id}/audit").json()["items"]
    assert rows and all(row["actor"] == "soufiane" for row in rows)
