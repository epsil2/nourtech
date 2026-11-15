"""GitHub App authentication for the Project Factory.

Three credentials exist in the GitHub App model and only one of them is the machine
credential:

* the **private key** signs a short-lived RS256 JWT, which mints an **installation
  access token** (one hour, scoped to the repositories the app is installed on). This is
  what a backend must use: no human in the loop, no long-lived secret in a request path.
* the **client id + client secret** drive the OAuth flows (authorization code, or device
  code). They produce *user* tokens, which represent a person, not the service — usable
  for a one-time consent, not as the factory's identity.
* a personal access token is the fallback, kept only so the host can still work while the
  App is being installed.

Nothing here logs a token, a secret or a JWT. Every failure raises OAuthError carrying the
HTTP status and GitHub's own message, so the operator sees the real cause.

The RS256 signature is produced by the `openssl` binary: the API image is small and
`cryptography` is not a dependency of this service. openssl is present everywhere this
runs (host and Debian-based image).
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

SECRETS_FILE = "/opt/agent-factory/secrets/github-app.env"
TOKEN_ENDPOINT = "https://github.com/login/oauth/access_token"
DEVICE_CODE_ENDPOINT = "https://github.com/login/device/code"
API = "https://api.github.com"


class OAuthError(RuntimeError):
    """A GitHub App call failed. Carries the status and GitHub's message, never a secret."""

    def __init__(self, message: str, *, status: int | None = None, body: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body

    def as_dict(self) -> dict[str, Any]:
        return {"error": str(self), "status": self.status, "body": self.body}


# --------------------------------------------------------------------------- #
# settings
# --------------------------------------------------------------------------- #
def _read_file(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        return {}
    return values


def setting(name: str) -> str:
    """Environment first, then the 0600 secrets file. Never a value from the browser."""
    value = (os.getenv(name) or "").strip()
    if value:
        return value
    return (_read_file(SECRETS_FILE).get(name) or "").strip()


def save_setting(name: str, value: str) -> None:
    """Persist one key into the 0600 secrets file, preserving the other lines.

    Only ever called server-side after a flow the server validated; no browser value is
    written without passing through an installation lookup first.
    """
    if not re.fullmatch(r"[A-Z0-9_]+", name):
        raise ValueError(f"invalid setting name: {name}")
    try:
        with open(SECRETS_FILE, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        lines = []
    prefix = f"{name}="
    replaced = False
    for index, line in enumerate(lines):
        if line.strip().startswith(prefix):
            lines[index] = f"{name}={value}"
            replaced = True
    if not replaced:
        lines.append(f"{name}={value}")
    directory = os.path.dirname(SECRETS_FILE) or "."
    os.makedirs(directory, mode=0o700, exist_ok=True)
    temporary = f"{SECRETS_FILE}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    os.replace(temporary, SECRETS_FILE)


def app_id() -> str:
    return setting("GITHUB_APP_ID")


def client_id() -> str:
    return setting("GITHUB_APP_CLIENT_ID")


def client_secret() -> str:
    return setting("GITHUB_APP_CLIENT_SECRET")


def private_key_path() -> str:
    return setting("GITHUB_APP_PRIVATE_KEY_PATH")


def installation_id() -> str:
    return setting("GITHUB_APP_INSTALLATION_ID")


def configured() -> tuple[bool, str]:
    """Is the *machine* credential complete? A client secret alone is not enough."""
    missing = [name for name, value in (
        ("GITHUB_APP_ID", app_id()),
        ("GITHUB_APP_PRIVATE_KEY_PATH", private_key_path()),
    ) if not value]
    if missing:
        return False, "GitHub App incomplet : " + ", ".join(missing) + " manquant(s)"
    if not os.path.exists(private_key_path()):
        return False, f"clé privée GitHub App introuvable : {private_key_path()}"
    return True, "GitHub App prête"


# --------------------------------------------------------------------------- #
# RS256 JWT, signed with openssl
# --------------------------------------------------------------------------- #
def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def sign_jwt(app: str, key_path: str, *, now: int | None = None, ttl: int = 540) -> str:
    """Build the RS256 assertion GitHub accepts as `Authorization: Bearer`.

    `iat` is backdated 60 s so a slightly fast host clock does not invalidate the token;
    the lifetime stays under GitHub's 10 minute ceiling.
    """
    issued = int(now if now is not None else time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    payload = {"iat": issued - 60, "exp": issued + ttl, "iss": str(app)}
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url(json.dumps(payload, separators=(",", ":")).encode())
    )
    try:
        signature = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", key_path],
            input=signing_input.encode(),
            capture_output=True,
            check=True,
        ).stdout
    except FileNotFoundError as exc:  # pragma: no cover - openssl is always installed
        raise OAuthError("openssl introuvable : impossible de signer l'assertion GitHub App") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode(errors="replace").strip()
        raise OAuthError(f"signature RS256 refusée par openssl : {detail}") from exc
    return f"{signing_input}.{_b64url(signature)}"


# --------------------------------------------------------------------------- #
# installation tokens
# --------------------------------------------------------------------------- #
def _call(method: str, url: str, *, headers: dict[str, str], body: dict[str, Any] | None = None,
          timeout: float = 30.0) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode() or "{}"
            return response.status, json.loads(raw) if raw.startswith(("{", "[")) else raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw[:400]
        return exc.code, parsed
    except urllib.error.URLError as exc:
        return 0, {"message": str(exc.reason)}


def _app_headers() -> dict[str, str]:
    ready, detail = configured()
    if not ready:
        raise OAuthError(detail)
    return {
        "Authorization": f"Bearer {sign_jwt(app_id(), private_key_path())}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "camila-project-factory",
        "Content-Type": "application/json",
    }


def installations() -> list[dict[str, Any]]:
    status, payload = _call("GET", f"{API}/app/installations?per_page=100", headers=_app_headers())
    if status != 200 or not isinstance(payload, list):
        raise OAuthError("lecture des installations GitHub App refusée", status=status, body=payload)
    return payload


def user_installations(access_token: str) -> list[dict[str, Any]]:
    """Installations the *human* just authorised, read with their user token.

    Used by the OAuth client flow to learn which installation id to bind; the token itself
    is never stored and never used for provisioning.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "camila-project-factory",
    }
    status, payload = _call("GET", f"{API}/user/installations?per_page=100", headers=headers)
    if status != 200 or not isinstance(payload, dict):
        raise OAuthError("lecture des installations de l'utilisateur refusée", status=status, body=payload)
    rows = payload.get("installations") or []
    return [row for row in rows if isinstance(row, dict)]


def resolve_installation(owner: str) -> str:
    """Installation id to use: the configured one, else the one covering `owner`."""
    explicit = installation_id()
    if explicit:
        return explicit
    for row in installations():
        account = (row.get("account") or {}).get("login") or ""
        if account.lower() == owner.lower():
            return str(row["id"])
    raise OAuthError(
        f"l'application GitHub n'est installée sur aucun compte nommé {owner} : "
        "installer l'app sur le compte puis relancer l'étape",
        status=404,
    )


_cache: dict[str, Any] = {"token": "", "expires_at": 0.0, "installation": ""}
_lock = threading.Lock()


def installation_token(owner: str, *, force: bool = False) -> str:
    """A one-hour token for the installation covering `owner`, cached until near expiry."""
    with _lock:
        if not force and _cache["token"] and _cache["expires_at"] - 60 > time.time():
            return _cache["token"]
        target = resolve_installation(owner)
        status, payload = _call(
            "POST", f"{API}/app/installations/{target}/access_tokens", headers=_app_headers()
        )
        if status not in (200, 201) or not isinstance(payload, dict) or not payload.get("token"):
            raise OAuthError("émission du jeton d'installation refusée", status=status, body=payload)
        expires = payload.get("expires_at") or ""
        _cache.update(token=payload["token"], expires_at=_parse_iso(expires), installation=target)
        return _cache["token"]


def _parse_iso(value: str) -> float:
    if not value:
        return time.time() + 3600
    try:
        return time.mktime(time.strptime(value.replace("Z", ""), "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return time.time() + 3600


def installation_identity(owner: str) -> dict[str, Any]:
    """What the installation token is allowed to do — used by the readiness probe."""
    token = installation_token(owner)
    status, payload = _call("GET", f"{API}/installation/repositories?per_page=1", headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "camila-project-factory",
    })
    if status != 200 or not isinstance(payload, dict):
        raise OAuthError("lecture des dépôts de l'installation refusée", status=status, body=payload)
    return {"installation": _cache["installation"], "repositories": payload.get("total_count", 0)}


# --------------------------------------------------------------------------- #
# OAuth client flow (user consent) — for the one-time authorisation, never the machine path
# --------------------------------------------------------------------------- #
def device_flow_start(scope: str = "repo workflow") -> dict[str, Any]:
    if not client_id():
        raise OAuthError("GITHUB_APP_CLIENT_ID manquant : le flux OAuth ne peut pas démarrer")
    status, payload = _call("POST", DEVICE_CODE_ENDPOINT, headers={
        "Accept": "application/json", "User-Agent": "camila-project-factory"}, body={
        "client_id": client_id(), "scope": scope})
    if status != 200 or not isinstance(payload, dict) or "device_code" not in payload:
        raise OAuthError("démarrage du flux device refusé", status=status, body=payload)
    return payload


def device_flow_poll(device_code: str) -> dict[str, Any]:
    """Exchange a device code for a user token. Returns {'status': 'pending'|'ok', ...}."""
    if not client_id() or not client_secret():
        raise OAuthError("GITHUB_APP_CLIENT_ID / GITHUB_APP_CLIENT_SECRET manquants")
    status, payload = _call("POST", TOKEN_ENDPOINT, headers={
        "Accept": "application/json", "User-Agent": "camila-project-factory"}, body={
        "client_id": client_id(),
        "client_secret": client_secret(),
        "device_code": device_code,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
    })
    if isinstance(payload, dict) and payload.get("error") in ("authorization_pending", "slow_down"):
        return {"status": "pending", "detail": payload.get("error")}
    if status != 200 or not isinstance(payload, dict) or not payload.get("access_token"):
        raise OAuthError("échange du code device refusé", status=status, body=payload)
    return {"status": "ok", **payload}


def exchange_code(code: str, redirect_uri: str = "") -> dict[str, Any]:
    """Authorization-code flow: only valid inside the browser redirect, kept for completeness."""
    if not client_id() or not client_secret():
        raise OAuthError("GITHUB_APP_CLIENT_ID / GITHUB_APP_CLIENT_SECRET manquants")
    body = {"client_id": client_id(), "client_secret": client_secret(), "code": code}
    if redirect_uri:
        body["redirect_uri"] = redirect_uri
    status, payload = _call("POST", TOKEN_ENDPOINT, headers={
        "Accept": "application/json", "User-Agent": "camila-project-factory"}, body=body)
    if status != 200 or not isinstance(payload, dict) or not payload.get("access_token"):
        raise OAuthError("échange du code OAuth refusé", status=status, body=payload)
    return payload


def refresh(refresh_token: str) -> dict[str, Any]:
    if not client_id() or not client_secret():
        raise OAuthError("GITHUB_APP_CLIENT_ID / GITHUB_APP_CLIENT_SECRET manquants")
    status, payload = _call("POST", TOKEN_ENDPOINT, headers={
        "Accept": "application/json", "User-Agent": "camila-project-factory"}, body={
        "client_id": client_id(),
        "client_secret": client_secret(),
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    })
    if status != 200 or not isinstance(payload, dict) or not payload.get("access_token"):
        raise OAuthError("rafraîchissement du jeton OAuth refusé", status=status, body=payload)
    return payload


def authorization_url(state: str = "", redirect_uri: str = "") -> str:
    params = {"client_id": client_id()}
    if redirect_uri:
        params["redirect_uri"] = redirect_uri
    if state:
        params["state"] = state
    return "https://github.com/login/oauth/authorize?" + urllib.parse.urlencode(params)
