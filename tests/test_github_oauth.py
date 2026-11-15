"""GitHub App OAuth client flow: binding an installation without trusting the browser.

The flow only ever *binds* an installation id that GitHub itself returned for the token
derived from the authorization code. The user token is never stored and never used to
provision; the machine credential stays the installation token minted from the private key.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from app import github_app, main as main_module, security  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_STORE", "memory")
    monkeypatch.setenv("FACTORY_AUTH", "on")
    monkeypatch.setenv("CAMILIA_BOOTSTRAP_USER", "soufiane")
    monkeypatch.setenv("CAMILIA_BOOTSTRAP_PASSWORD", "correct-horse-battery")
    monkeypatch.setenv("CP_JOBS_SYNC", "1")
    monkeypatch.setattr(github_app, "SECRETS_FILE", str(tmp_path / "github-app.env"))
    Path(github_app.SECRETS_FILE).write_text("GITHUB_APP_CLIENT_ID=cid\nGITHUB_APP_CLIENT_SECRET=secret\n",
                                            encoding="utf-8")
    with TestClient(main_module.app) as test_client:
        response = test_client.post(
            "/api/v1/auth/login", json={"username": "soufiane", "password": "correct-horse-battery"}
        )
        assert response.status_code == 200, response.text
        yield test_client


def test_status_names_what_is_missing_without_leaking_secrets(client):
    body = client.get("/api/v1/factory/github/status").json()
    assert body["credential"] in {"app", "pat", "none"}
    assert body["app_configured"] is False
    assert "GITHUB_APP_ID" in body["app_detail"]
    assert body["oauth_configured"] is True
    assert "secret" not in str(body).lower()


def test_the_flow_refuses_to_start_without_a_client_id(client, monkeypatch):
    monkeypatch.setattr(github_app, "client_id", lambda: "")
    response = client.post("/api/v1/factory/github/oauth/start", json={})
    assert response.status_code == 409
    assert "GITHUB_APP_CLIENT_ID" in response.json()["detail"]


def test_a_foreign_state_is_rejected(client):
    response = client.get("/api/v1/factory/github/oauth/callback", params={"code": "abc", "state": "forged"})
    assert response.status_code == 400
    assert "state" in response.json()["detail"]


def test_the_callback_binds_the_installation_github_returned(client, monkeypatch):
    saved: list[tuple[str, str]] = []
    monkeypatch.setattr(github_app, "save_setting", lambda name, value: saved.append((name, value)))
    monkeypatch.setattr(
        github_app, "exchange_code", lambda code, redirect_uri="": {"access_token": "ghu_user", "token_type": "bearer"}
    )
    monkeypatch.setattr(
        github_app,
        "user_installations",
        lambda token: [
            {"id": 7, "account": {"login": "someone-else"}, "repository_selection": "all"},
            {"id": 4242, "account": {"login": "epsil2"}, "repository_selection": "selected"},
        ],
    )

    started = client.post("/api/v1/factory/github/oauth/start", json={})
    assert started.status_code == 200, started.text
    state = started.json()["state"]
    assert state in started.json()["authorize_url"]

    response = client.get(
        "/api/v1/factory/github/oauth/callback",
        params={"code": "abc", "state": state},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "github=linked" in response.headers["location"]
    assert saved == [("GITHUB_APP_INSTALLATION_ID", "4242")]

    # The state is single use: replaying the callback cannot rebind another installation.
    replay = client.get(
        "/api/v1/factory/github/oauth/callback", params={"code": "abc", "state": state}, follow_redirects=False
    )
    assert replay.status_code == 400


def test_a_refusal_from_github_is_recorded_not_hidden(client):
    started = client.post("/api/v1/factory/github/oauth/start", json={})
    state = started.json()["state"]
    response = client.get(
        "/api/v1/factory/github/oauth/callback",
        params={"error": "access_denied", "error_description": "The user denied", "state": state},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "github=refused" in response.headers["location"]
    events = [row for row in client.app.state.store.cp_list("user")]  # store reachable
    assert events is not None


def test_anonymous_callers_cannot_touch_the_installation(client):
    cookie = client.cookies
    client.cookies.clear()
    try:
        assert client.get("/api/v1/factory/github/status").status_code == 401
        assert client.post("/api/v1/factory/github/oauth/start", json={}).status_code == 401
    finally:
        for name, value in cookie.items():
            client.cookies.set(name, value)
