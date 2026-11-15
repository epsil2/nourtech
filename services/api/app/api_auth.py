"""Authentication routes: login, logout, who-am-I, password change, users.

The cookie is the only credential the browser holds. It is ``HttpOnly`` and
``SameSite=Lax``; ``Secure`` is set from ``X-Forwarded-Proto`` because TLS terminates
at the proxy. No token is ever returned in a JSON body a script could stash.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import security
from .redaction import redact
from .security import (
    ROLES,
    SESSION_COOKIE,
    SESSION_TTL_HOURS,
    actor_of,
    create_session,
    create_user,
    find_user,
    public_user,
    require_roles,
    require_user,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class PasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class UserIn(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    password: str = Field(min_length=8, max_length=256)
    role: str = "viewer"
    email: str = Field(default="", max_length=200)


def _ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "")


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response) -> dict[str, Any]:
    store = request.app.state.store
    user = security.authenticate(store, body.username, body.password, _ip(request))
    if not user:
        store.add_audit(
            (body.username or "")[:64],
            "auth.login_failed",
            "user",
            "",
            redact({"ip": _ip(request)}),
        )
        raise HTTPException(status_code=401, detail="invalid credentials")
    token, session = create_session(
        store, user, ip=_ip(request), agent=request.headers.get("user-agent", "")
    )
    secure = (request.headers.get("x-forwarded-proto") or request.url.scheme) == "https"
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_HOURS * 3600,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )
    store.add_audit(
        user["username"],
        "auth.login",
        "user",
        user["id"],
        redact({"ip": _ip(request), "agent": request.headers.get("user-agent", "")}),
    )
    return {"user": public_user(user), "expires_at": session.get("expires_at")}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict[str, Any]:
    store = request.app.state.store
    session = getattr(request.state, "session", None)
    if session:
        security.revoke_session(store, session["id"])
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request) -> dict[str, Any]:
    user = security.current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="not authenticated")
    return {"user": public_user(user)}


@router.post("/password")
def change_password(body: PasswordIn, request: Request) -> dict[str, Any]:
    store = request.app.state.store
    user = require_user(request)
    if not security.verify_password(body.current_password, user.get("password_hash", "")):
        raise HTTPException(status_code=400, detail="current password is wrong")
    security.set_password(store, user["username"], body.new_password)
    store.add_audit(actor_of(request), "auth.password_changed", "user", user["id"], {})
    return {"ok": True}


@router.get("/users")
def list_users(request: Request, _: dict = Depends(require_roles("admin"))) -> dict[str, Any]:
    store = request.app.state.store
    rows = store.cp_list("user", limit=200)
    return {"items": [public_user(u) for u in rows], "roles": list(ROLES)}


@router.post("/users", status_code=201)
def add_user(body: UserIn, request: Request, _: dict = Depends(require_roles("admin"))) -> dict[str, Any]:
    store = request.app.state.store
    if body.role not in ROLES:
        raise HTTPException(status_code=422, detail=f"role must be one of {', '.join(ROLES)}")
    if find_user(store, body.username):
        raise HTTPException(status_code=409, detail="username already exists")
    try:
        user = create_user(store, body.username, body.password, role=body.role, email=body.email)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    store.add_audit(
        actor_of(request), "auth.user_created", "user", user["id"], {"role": body.role}
    )
    return {"user": public_user(user)}


@router.get("/config")
def auth_config() -> dict[str, Any]:
    """What the login screen is allowed to know."""
    return {
        "auth_required": security.auth_enforced(),
        "bootstrap_configured": bool(os.getenv("CAMILIA_BOOTSTRAP_PASSWORD")),
    }
