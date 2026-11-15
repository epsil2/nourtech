"""GitHub App credential handling: JWT assertion, installation tokens, OAuth flows.

The signature is verified with openssl against the public key, so the test proves the
assertion GitHub receives is genuinely RS256-signed with the App's private key — not just
shaped like a JWT.
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from app import github_app  # noqa: E402


@pytest.fixture()
def rsa_key(tmp_path: Path) -> tuple[Path, Path]:
    private = tmp_path / "app.pem"
    public = tmp_path / "app.pub"
    subprocess.run(["openssl", "genrsa", "-out", str(private), "2048"], check=True, capture_output=True)
    subprocess.run(["openssl", "rsa", "-in", str(private), "-pubout", "-out", str(public)],
                   check=True, capture_output=True)
    private.chmod(0o600)
    return private, public


def _part(token: str, index: int) -> dict:
    chunk = token.split(".")[index]
    chunk += "=" * (-len(chunk) % 4)
    return json.loads(base64.urlsafe_b64decode(chunk))


def test_assertion_is_signed_with_the_private_key(rsa_key):
    private, public = rsa_key
    token = github_app.sign_jwt("123456", str(private), now=1_800_000_000, ttl=540)

    header, payload = _part(token, 0), _part(token, 1)
    assert header == {"alg": "RS256", "typ": "JWT"}
    assert payload["iss"] == "123456"
    assert payload["exp"] - payload["iat"] == 600  # 540s of life plus the 60s backdate
    assert payload["iat"] < 1_800_000_000

    signing_input, signature = token.rsplit(".", 1)
    raw_signature = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
    verified = subprocess.run(
        ["openssl", "dgst", "-sha256", "-verify", str(public), "-signature", "/dev/stdin"],
        input=raw_signature,
        capture_output=True,
    )
    # /dev/stdin cannot carry both payload and signature, so verify through temp files.
    import tempfile

    with tempfile.NamedTemporaryFile(delete=False) as sig_file, \
            tempfile.NamedTemporaryFile(delete=False) as data_file:
        sig_file.write(raw_signature)
        data_file.write(signing_input.encode())
    result = subprocess.run(
        ["openssl", "dgst", "-sha256", "-verify", str(public), "-signature", sig_file.name, data_file.name],
        capture_output=True,
    )
    assert b"Verified OK" in result.stdout, result.stderr


def test_a_client_secret_alone_is_not_a_machine_credential(tmp_path, monkeypatch):
    secrets = tmp_path / "github-app.env"
    secrets.write_text("GITHUB_APP_CLIENT_SECRET=deadbeef\n", encoding="utf-8")
    secrets.chmod(0o600)
    monkeypatch.setattr(github_app, "SECRETS_FILE", str(secrets))
    for name in ("GITHUB_APP_ID", "GITHUB_APP_PRIVATE_KEY_PATH", "GITHUB_APP_INSTALLATION_ID"):
        monkeypatch.delenv(name, raising=False)

    ready, detail = github_app.configured()
    assert ready is False
    assert "GITHUB_APP_ID" in detail and "GITHUB_APP_PRIVATE_KEY_PATH" in detail
    assert github_app.client_secret() == "deadbeef"


def test_installation_token_is_cached_and_reused(tmp_path, monkeypatch):
    private = tmp_path / "app.pem"
    subprocess.run(["openssl", "genrsa", "-out", str(private), "2048"], check=True, capture_output=True)
    monkeypatch.setenv("GITHUB_APP_ID", "999")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", str(private))
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "42")
    github_app._cache.update(token="", expires_at=0.0, installation="")

    calls: list[tuple[str, str]] = []

    def fake_call(method, url, *, headers, body=None, timeout=30.0):
        calls.append((method, url))
        assert headers["Authorization"].startswith("Bearer ey")
        return 201, {"token": "ghs_test", "expires_at": "2999-01-01T00:00:00Z"}

    monkeypatch.setattr(github_app, "_call", fake_call)
    assert github_app.installation_token("epsil2") == "ghs_test"
    assert github_app.installation_token("epsil2") == "ghs_test"
    assert len(calls) == 1, calls  # the second call is served from the cache


def test_refused_token_minting_surfaces_githubs_message(tmp_path, monkeypatch):
    private = tmp_path / "app.pem"
    subprocess.run(["openssl", "genrsa", "-out", str(private), "2048"], check=True, capture_output=True)
    monkeypatch.setenv("GITHUB_APP_ID", "999")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", str(private))
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "42")
    github_app._cache.update(token="", expires_at=0.0, installation="")

    monkeypatch.setattr(github_app, "_call", lambda *a, **k: (404, {"message": "Not Found"}))
    with pytest.raises(github_app.OAuthError) as excinfo:
        github_app.installation_token("epsil2")
    assert excinfo.value.status == 404
    assert "Not Found" in json.dumps(excinfo.value.as_dict())


def test_the_factory_reports_which_credential_it_would_use(monkeypatch):
    import app.project_factory as factory

    monkeypatch.setenv("GITHUB_APP_ID", "")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", "")
    monkeypatch.setattr(github_app, "configured", lambda: (False, "GitHub App incomplet"))
    mode, detail = factory.github_credential()
    assert mode in {"pat", "none"}
    if mode == "pat":
        assert "jeton personnel" in detail
