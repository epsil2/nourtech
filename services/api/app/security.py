"""Server-side identity, sessions and role-based access (Phase 1 prerequisite).

The dashboard used to sit behind a single nginx basic-auth credential, and every
backend route took the caller's name from an ``x-actor`` header.  That is not an
identity: any client could type another name and the audit trail would record it.

This module replaces that with an identity the server can verify:

* a user row carries a salted PBKDF2-SHA256 hash — no plaintext, ever;
* logging in mints a random session token, stored only as its SHA-256 digest, and
  returned to the browser as an ``HttpOnly`` cookie;
* every request resolves that cookie back to a user, and the *server* decides who
  the actor is.  ``x-actor`` is ignored when authentication is enforced;
* roles are checked in the route dependency, not in the browser.

Storage reuses the control-plane document table (``cp_documents``) so the same code
works against the in-memory store used by the tests and PostgreSQL in production.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from fastapi import HTTPException, Request

ROLES = ("admin", "operator", "viewer")

# Minimum password length, applied at hashing time and mirrored by the API schemas
# (api_auth imports it) so the two can never drift apart. Six is the operator's
# explicit choice of policy floor for this single-tenant deployment.
MIN_PASSWORD_LENGTH = 6
DEFAULT_ROLE = "viewer"

SESSION_COOKIE = "cam_session"
SESSION_TTL_HOURS = 12
PBKDF2_ROUNDS = 240_000

# login throttling: per (username, ip), reset on success
_MAX_FAILURES = 5
_FAILURE_WINDOW = timedelta(minutes=5)

_ATTEMPTS: dict[str, list[datetime]] = {}
_ATTEMPTS_LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #
def auth_enforced() -> bool:
    """Authentication is on unless a test explicitly turns it off.

    Default-on matters: a misconfigured deployment must fail closed, not open.
    """
    return (os.getenv("FACTORY_AUTH", "on").strip().lower() not in {"off", "0", "false"})


def bootstrap_credentials() -> tuple[str, str, str] | None:
    """Initial administrator, read from the environment only."""
    password = (os.getenv("CAMILIA_BOOTSTRAP_PASSWORD") or "").strip()
    if not password:
        return None
    user = (os.getenv("CAMILIA_BOOTSTRAP_USER") or "soufiane").strip().lower()[:64]
    role = (os.getenv("CAMILIA_BOOTSTRAP_ROLE") or "admin").strip().lower()
    if role not in ROLES:
        role = "admin"
    return user, password, role


# --------------------------------------------------------------------------- #
# password hashing
# --------------------------------------------------------------------------- #
def hash_password(password: str, *, rounds: int = PBKDF2_ROUNDS) -> str:
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), rounds)
    return f"pbkdf2_sha256${rounds}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, rounds, salt, digest = stored.split("$", 3)
    except (AttributeError, ValueError):
        return False
    if scheme != "pbkdf2_sha256":
        return False
    try:
        computed = hashlib.pbkdf2_hmac("sha256", (password or "").encode(), bytes.fromhex(salt), int(rounds))
    except ValueError:
        return False
    return hmac.compare_digest(computed.hex(), digest)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --------------------------------------------------------------------------- #
# throttling
# --------------------------------------------------------------------------- #
def _throttle_key(username: str, ip: str) -> str:
    return f"{username.lower()}|{ip}"


def login_blocked(username: str, ip: str) -> bool:
    now = datetime.now(timezone.utc)
    with _ATTEMPTS_LOCK:
        hits = [t for t in _ATTEMPTS.get(_throttle_key(username, ip), []) if now - t < _FAILURE_WINDOW]
        _ATTEMPTS[_throttle_key(username, ip)] = hits
        return len(hits) >= _MAX_FAILURES


def record_failure(username: str, ip: str) -> None:
    now = datetime.now(timezone.utc)
    with _ATTEMPTS_LOCK:
        _ATTEMPTS.setdefault(_throttle_key(username, ip), []).append(now)


def clear_failures(username: str, ip: str) -> None:
    with _ATTEMPTS_LOCK:
        _ATTEMPTS.pop(_throttle_key(username, ip), None)


def reset_throttle() -> None:
    """Tests only."""
    with _ATTEMPTS_LOCK:
        _ATTEMPTS.clear()


# --------------------------------------------------------------------------- #
# users and sessions, stored as control-plane documents
# --------------------------------------------------------------------------- #
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _store(request: Request):
    return request.app.state.store


def find_user(store, username: str) -> dict[str, Any] | None:
    name = (username or "").strip().lower()
    if not name:
        return None
    rows = store.cp_list("user", where={"username": name}, limit=1)
    return rows[0] if rows else None


def create_user(
    store,
    username: str,
    password: str,
    *,
    role: str = DEFAULT_ROLE,
    email: str = "",
) -> dict[str, Any]:
    name = (username or "").strip().lower()
    if not name or len(name) > 64:
        raise ValueError("invalid username")
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    if find_user(store, name):
        raise ValueError("username already exists")
    doc = {
        "username": name,
        "email": (email or "")[:200],
        "role": role,
        "password_hash": hash_password(password),
        "disabled": False,
        "created_at": _now(),
        "last_login_at": None,
    }
    return store.cp_save("user", doc)


def set_password(store, username: str, password: str) -> bool:
    user = find_user(store, username)
    if not user:
        return False
    store.cp_patch(
        "user",
        user["id"],
        {"password_hash": hash_password(password), "password_changed_at": _now()},
    )
    revoke_all_sessions(store, user["id"])
    return True


def bootstrap(store) -> dict[str, Any] | None:
    """Create the first administrator when none exists.

    Fails closed: without ``CAMILIA_BOOTSTRAP_PASSWORD`` nothing is created and every
    protected route stays closed.
    """
    creds = bootstrap_credentials()
    if not creds:
        return None
    username, password, role = creds
    existing = find_user(store, username)
    if existing:
        return None
    try:
        return create_user(store, username, password, role=role)
    except ValueError:
        return None


def authenticate(store, username: str, password: str, ip: str = "") -> dict[str, Any] | None:
    if login_blocked(username, ip):
        raise HTTPException(status_code=429, detail="too many attempts, try again later")
    user = find_user(store, username)
    if not user or user.get("disabled"):
        record_failure(username, ip)
        return None
    if not verify_password(password, user.get("password_hash", "")):
        record_failure(username, ip)
        return None
    clear_failures(username, ip)
    store.cp_patch("user", user["id"], {"last_login_at": _now()})
    return user


def create_session(store, user: dict[str, Any], *, ip: str = "", agent: str = "") -> tuple[str, dict[str, Any]]:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)
    doc = {
        "token_hash": token_digest(token),
        "user_id": user["id"],
        "username": user.get("username"),
        "expires_at": expires.isoformat(),
        "ip": (ip or "")[:64],
        "user_agent": (agent or "")[:200],
        "revoked_at": None,
        "created_at": _now(),
    }
    saved = store.cp_save("session", doc)
    return token, saved


def resolve_session(store, token: str) -> dict[str, Any] | None:
    if not token:
        return None
    rows = store.cp_list("session", where={"token_hash": token_digest(token)}, limit=1)
    if not rows:
        return None
    session = rows[0]
    if session.get("revoked_at"):
        return None
    try:
        expires = datetime.fromisoformat(str(session.get("expires_at")))
    except ValueError:
        return None
    if expires <= datetime.now(timezone.utc):
        return None
    user = store.cp_get("user", session["user_id"])
    if not user or user.get("disabled"):
        return None
    return {"user": user, "session": session}


def revoke_session(store, session_id: str) -> None:
    store.cp_patch("session", session_id, {"revoked_at": _now()})


def revoke_all_sessions(store, user_id: str) -> int:
    count = 0
    for session in store.cp_list("session", where={"user_id": user_id}, limit=200):
        if not session.get("revoked_at"):
            revoke_session(store, session["id"])
            count += 1
    return count


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user.get("id"),
        "username": user.get("username"),
        "email": user.get("email") or "",
        "role": user.get("role"),
        "last_login_at": user.get("last_login_at"),
    }


# --------------------------------------------------------------------------- #
# request dependencies
# --------------------------------------------------------------------------- #
PUBLIC_PREFIXES = (
    "/healthz",
    "/readyz",
    "/api/v1/health",
    "/api/v1/auth/login",
    "/api/v1/auth/me",
    "/api/v1/auth/config",
    "/openapi.json",
    "/docs",
    "/redoc",
)


def is_public(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in PUBLIC_PREFIXES)


def current_user(request: Request) -> dict[str, Any] | None:
    """Resolve the caller from the session cookie. Cached on the request state."""
    cached = getattr(request.state, "user", None)
    if cached is not None:
        return cached
    store = request.app.state.store
    token = request.cookies.get(SESSION_COOKIE, "")
    found = resolve_session(store, token)
    if not found:
        return None
    request.state.user = found["user"]
    request.state.session = found["session"]
    return found["user"]


def require_user(request: Request) -> dict[str, Any]:
    if not auth_enforced():
        # Tests only: a synthetic identity so route logic still has an actor.
        synthetic = {"id": "auth-disabled", "username": "local-test", "role": "admin"}
        request.state.user = synthetic
        return synthetic
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="authentication required")
    return user


def require_roles(*roles: str):
    def dependency(request: Request) -> dict[str, Any]:
        user = require_user(request)
        if user.get("role") not in roles:
            raise HTTPException(status_code=403, detail="insufficient role")
        return user

    return dependency


def actor_of(request: Request) -> str:
    """The actor for audit rows: verified identity, never a client header."""
    user = getattr(request.state, "user", None)
    if isinstance(user, dict) and user.get("username"):
        return str(user["username"])[:120]
    if not auth_enforced():
        return (request.headers.get("x-actor") or "anonymous").strip()[:120]
    return "anonymous"


def role_allows(user: dict[str, Any], allowed: Iterable[str]) -> bool:
    return user.get("role") in set(allowed)


# --------------------------------------------------------------------------- #
# ASGI guard
# --------------------------------------------------------------------------- #
class AuthGuard:
    """Refuse every non-public route without a valid session.

    Route-level dependencies stay in place for role checks; this guard is the net
    that catches a router someone forgets to protect.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not auth_enforced():
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if not path.startswith("/api/") or is_public(path):
            await self.app(scope, receive, send)
            return

        from starlette.requests import Request as StarletteRequest
        from starlette.responses import JSONResponse

        request = StarletteRequest(scope, receive)
        if current_user(request) is None:
            response = JSONResponse({"detail": "authentication required"}, status_code=401)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
