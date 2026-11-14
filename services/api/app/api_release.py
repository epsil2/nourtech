"""Release endpoints (SCRUM-47, SCRUM-48, SCRUM-49).

The order is draft -> approved -> published, and a rollback can only point at a
release that was itself published. Each step is audited with its actor: a release
that nobody signed is not a release.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import release as rules

router = APIRouter(prefix="/api/v1", tags=["releases"])


class TestsIn(BaseModel):
    passed: int = Field(ge=0)
    failed: int = Field(default=0, ge=0)
    run_id: str = Field(default="", max_length=40)


class StoryIn(BaseModel):
    key: str = Field(min_length=6, max_length=12)
    summary: str = Field(default="", max_length=200)
    status: str = Field(default="", max_length=40)
    type: str = Field(default="Story", max_length=20)


class ArtifactIn(BaseModel):
    version: str = Field(min_length=5, max_length=20)
    commit_sha: str = Field(min_length=40, max_length=40)
    tests: TestsIn
    stories: list[StoryIn] = Field(min_length=1, max_length=100)
    workflow: str = Field(default=rules.TRUSTED_WORKFLOW, max_length=40)
    source: str = Field(default="github", max_length=20)
    image: str = Field(default="", max_length=200)
    built_at: str | None = Field(default=None, max_length=40)


class ReadinessIn(BaseModel):
    commit_sha: str = Field(min_length=40, max_length=40)
    target: str = Field(default="production", max_length=20)
    stories: list[StoryIn] = Field(min_length=1, max_length=100)
    blockers: list[str] = Field(default_factory=list, max_length=50)


class RollbackIn(BaseModel):
    target_release_id: str = Field(min_length=10, max_length=64)
    reason: str = Field(min_length=5, max_length=300)
    schema_compatible: bool = False


def _store(request: Request):
    return request.app.state.store


def _artifact(store, commit_sha: str) -> dict[str, Any]:
    row = store.get_artifact(commit_sha)
    if row is None:
        raise HTTPException(status_code=404, detail="artifact not registered for this commit")
    return row


def _release(store, release_id: str) -> dict[str, Any]:
    row = store.get_release(release_id)
    if row is None:
        raise HTTPException(status_code=404, detail="release not found")
    return row


def _evaluations(store, commit_sha: str) -> list[dict[str, Any]]:
    return store.list_evaluations(commit_sha, 20)


@router.post("/artifacts", status_code=201)
def register_artifact(body: ArtifactIn, request: Request, x_actor: str | None = Header(default=None)) -> dict[str, Any]:
    store = _store(request)
    try:
        artifact = rules.build_artifact(
            version=body.version,
            commit_sha=body.commit_sha,
            workflow=body.workflow,
            source=body.source,
            image=body.image,
            built_at=body.built_at,
            tests=body.tests.model_dump(),
            stories=[s.model_dump() for s in body.stories],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    existing = store.get_artifact(artifact["commit_sha"])
    if existing:
        if existing.get("signature") != artifact["signature"]:
            raise HTTPException(
                status_code=409,
                detail="artifact already registered with a different signature: artifacts are immutable",
            )
        return existing
    row = store.create_artifact(artifact)
    store.add_audit((x_actor or "anonymous")[:120], "artifact.registered", "artifact", artifact["commit_sha"], {"version": artifact["version"]})
    return row


@router.get("/artifacts")
def list_artifacts(request: Request, limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    rows = _store(request).list_artifacts(limit)
    return {"items": rows, "count": len(rows)}


@router.get("/artifacts/{commit_sha}")
def get_artifact(commit_sha: str, request: Request) -> dict[str, Any]:
    return _artifact(_store(request), commit_sha)


@router.get("/artifacts/{commit_sha}/verify")
def verify_artifact(commit_sha: str, request: Request) -> dict[str, Any]:
    return rules.verify_artifact(_artifact(_store(request), commit_sha))


@router.post("/releases/readiness")
def readiness(body: ReadinessIn, request: Request) -> dict[str, Any]:
    store = _store(request)
    artifact = _artifact(store, body.commit_sha)
    try:
        return rules.readiness(
            artifact=artifact,
            stories=[s.model_dump() for s in body.stories],
            blockers=body.blockers,
            target=body.target,
            evaluations=_evaluations(store, artifact["commit_sha"]),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/releases")
def list_releases(request: Request, commit_sha: str | None = Query(default=None), limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    rows = _store(request).list_releases(commit_sha, limit)
    return {"items": rows, "count": len(rows)}


@router.get("/releases/{release_id}")
def get_release(release_id: str, request: Request) -> dict[str, Any]:
    return _release(_store(request), release_id)


@router.post("/releases/plan")
def plan_release(body: ReadinessIn, request: Request, x_actor: str | None = Header(default=None)) -> dict[str, Any]:
    """Record a draft release only when the readiness report is positive."""
    store = _store(request)
    artifact = _artifact(store, body.commit_sha)
    report = rules.readiness(
        artifact=artifact,
        stories=[s.model_dump() for s in body.stories],
        blockers=body.blockers,
        target=body.target,
        evaluations=_evaluations(store, artifact["commit_sha"]),
    )
    if not report["ready"]:
        raise HTTPException(status_code=409, detail={"message": "release not ready", "missing": report["missing"]})
    row = store.create_release(
        version=artifact["version"], commit_sha=artifact["commit_sha"], target=body.target, report=report
    )
    store.add_audit((x_actor or "anonymous")[:120], "release.planned", "release", row["id"], {"version": row["version"]})
    return row


@router.post("/releases/{release_id}/approve")
def approve(release_id: str, request: Request, x_actor: str | None = Header(default=None)) -> dict[str, Any]:
    store = _store(request)
    row = _release(store, release_id)
    if row["status"] != "draft":
        raise HTTPException(status_code=409, detail=f"release is {row['status']!r}, not 'draft'")
    if not (row.get("report") or {}).get("ready"):
        raise HTTPException(status_code=409, detail="readiness report is negative: a human cannot approve what is missing")
    updated = store.set_release(release_id, status="approved", approved_by=(x_actor or "anonymous")[:120])
    store.add_audit((x_actor or "anonymous")[:120], "release.approved", "release", release_id, {"version": row["version"]})
    return updated


@router.post("/releases/{release_id}/notes")
def generate_notes(release_id: str, request: Request, x_actor: str | None = Header(default=None)) -> dict[str, Any]:
    store = _store(request)
    row = _release(store, release_id)
    if row["status"] not in ("approved", "published"):
        raise HTTPException(status_code=409, detail=f"release is {row['status']!r}: approve it before writing notes")
    artifact = _artifact(store, row["commit_sha"])
    stories = (row.get("report") or {}).get("stories_detail") or [
        {"key": k, "summary": "", "status": "Done", "type": "Story"} for k in artifact.get("stories", [])
    ]
    text = rules.notes(version=row["version"], commit_sha=row["commit_sha"], stories=stories, built_at=artifact["built_at"])
    try:
        rules.verify_notes(text, artifact)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    updated = store.set_release(release_id, notes=text)
    store.add_audit((x_actor or "anonymous")[:120], "release.notes", "release", release_id, {"stories": artifact["stories"]})
    return updated


@router.post("/releases/{release_id}/publish")
def publish(release_id: str, request: Request, x_actor: str | None = Header(default=None)) -> dict[str, Any]:
    store = _store(request)
    row = _release(store, release_id)
    if row["status"] != "approved":
        raise HTTPException(status_code=409, detail=f"release is {row['status']!r}, not 'approved'")
    if not (row.get("notes") or "").strip():
        raise HTTPException(status_code=409, detail="release notes are missing: publish what you can describe")
    updated = store.set_release(release_id, status="published", published_at=rules._now())
    store.add_audit((x_actor or "anonymous")[:120], "release.published", "release", release_id, {"version": row["version"]})
    return updated


@router.post("/releases/{release_id}/rollback")
def rollback(release_id: str, body: RollbackIn, request: Request, x_actor: str | None = Header(default=None)) -> dict[str, Any]:
    store = _store(request)
    row = _release(store, release_id)
    if row["status"] != "published":
        raise HTTPException(status_code=409, detail=f"release is {row['status']!r}: only a published release can be rolled back")
    previous = store.get_release(body.target_release_id)
    try:
        plan = rules.rollback_plan(current=row, previous=previous, schema_compatible=body.schema_compatible)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    updated = store.set_release(
        release_id,
        status="rolled_back",
        rolled_back_to=plan["to_release"],
        rollback_reason=body.reason,
    )
    store.add_audit((x_actor or "anonymous")[:120], "release.rolled_back", "release", release_id, plan)
    return {"release": updated, "plan": plan}
