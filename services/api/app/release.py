"""Provenance, readiness, notes and rollback for a release (SCRUM-47, SCRUM-48, SCRUM-49).

Three rules shape this module:

* an artifact is immutable and carries its own signature, so a modified row is
  detectable instead of silent;
* readiness can only report what is missing — it never approves anything, because
  production has a human Go/No-Go gate;
* release notes are checked against the artifact they describe, not against the
  intention of the person writing them.

Everything is deterministic and offline: no registry, no model, no network call.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

HEX40 = re.compile(r"^[0-9a-f]{40}$")
VERSION = re.compile(r"^\d+\.\d+\.\d+$")
KEY = re.compile(r"^SCRUM-\d+$")

# Only artifacts produced by the approved GitHub Actions workflow may be promoted.
TRUSTED_WORKFLOW = "ci"
# A GitLab mirror push must never reach an environment.
ALLOWED_SOURCES = {"github"}
TARGETS = {"dev", "production"}
DONE_STATES = {"done", "terminé", "termine"}

SCHEMA_VERSION = "1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def check_commit(commit_sha: str) -> str:
    value = (commit_sha or "").strip().lower()
    if not HEX40.match(value):
        raise ValueError("commit_sha must be a full 40-character lowercase git sha")
    return value


def check_version(version: str) -> str:
    value = (version or "").strip()
    if not VERSION.match(value):
        raise ValueError("version must look like 1.4.0")
    return value


def check_stories(stories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for story in stories:
        key = str(story.get("key", "")).strip().upper()
        if not KEY.match(key):
            raise ValueError(f"invalid story key: {story.get('key')!r}")
        if key in seen:
            raise ValueError(f"story listed twice: {key}")
        seen.add(key)
        cleaned.append(
            {
                "key": key,
                "summary": str(story.get("summary", "")).strip()[:200],
                "status": str(story.get("status", "")).strip(),
                "type": str(story.get("type", "Story")).strip() or "Story",
            }
        )
    if not cleaned:
        raise ValueError("a release must list at least one story")
    return cleaned


def build_artifact(
    *,
    version: str,
    commit_sha: str,
    workflow: str,
    source: str,
    image: str,
    built_at: str | None,
    tests: dict[str, Any],
    stories: list[dict[str, Any]],
) -> dict[str, Any]:
    """Describe one build. The signature covers every field that matters later."""
    version = check_version(version)
    commit_sha = check_commit(commit_sha)
    if workflow != TRUSTED_WORKFLOW:
        raise ValueError(f"only artifacts built by the {TRUSTED_WORKFLOW!r} workflow may be promoted")
    if source not in ALLOWED_SOURCES:
        raise ValueError("a GitLab mirror change does not trigger a deployment")
    passed = int(tests.get("passed", 0) or 0)
    failed = int(tests.get("failed", 0) or 0)
    if passed <= 0:
        raise ValueError("an artifact without a passing test summary proves nothing")
    if failed:
        raise ValueError(f"{failed} test(s) failed: the artifact is not promotable")
    body = {
        "version": version,
        "commit_sha": commit_sha,
        "workflow": workflow,
        "source": source,
        "image": image or f"agent-factory-api:{commit_sha}",
        "built_at": built_at or _now(),
        "tests": {"passed": passed, "failed": failed, "run_id": str(tests.get("run_id", ""))[:40]},
        "stories": [s["key"] for s in check_stories(stories)],
    }
    body["schema_version"] = SCHEMA_VERSION
    body["signature"] = signature(body)
    return body


def signature(artifact: dict[str, Any]) -> str:
    covered = {k: artifact[k] for k in ("version", "commit_sha", "workflow", "source", "image", "built_at", "tests", "stories")}
    covered["schema_version"] = artifact.get("schema_version", SCHEMA_VERSION)
    return "sha256:" + hashlib.sha256(_canonical(covered).encode()).hexdigest()


def verify_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    expected = signature(artifact)
    stored = artifact.get("signature", "")
    return {
        "commit_sha": artifact.get("commit_sha"),
        "verified": bool(stored) and expected == stored,
        "expected": expected,
        "stored": stored,
    }


def readiness(
    *,
    artifact: dict[str, Any],
    stories: list[dict[str, Any]],
    blockers: list[str],
    target: str,
    evaluations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Report what is missing. Deliberately unable to approve."""
    stories = check_stories(stories)
    if target not in TARGETS:
        raise ValueError(f"target must be one of {sorted(TARGETS)}")
    verdict = verify_artifact(artifact)
    checks = [
        {
            "id": "artifact",
            "ok": verdict["verified"],
            "detail": "signature verified" if verdict["verified"] else "artifact signature does not match its content",
        },
        {
            "id": "tests",
            "ok": artifact["tests"]["passed"] > 0 and artifact["tests"]["failed"] == 0,
            "detail": f"{artifact['tests']['passed']} passed, {artifact['tests']['failed']} failed",
        },
        {
            "id": "stories",
            "ok": all(s["status"].lower() in DONE_STATES for s in stories),
            "detail": ", ".join(f"{s['key']}={s['status'] or 'unknown'}" for s in stories),
        },
        {
            "id": "blockers",
            "ok": not blockers,
            "detail": "none open" if not blockers else "; ".join(str(b)[:80] for b in blockers),
        },
        {
            "id": "evidence",
            "ok": any(e.get("passed") for e in evaluations),
            "detail": f"{len(evaluations)} evaluation(s) on this commit"
            if evaluations
            else "no evaluation recorded for this commit",
        },
        {
            "id": "target",
            "ok": True,
            "detail": target,
        },
    ]
    missing = [c["id"] for c in checks if not c["ok"]]
    if set(missing) & {"artifact", "tests", "evidence"}:
        risk = "high"
    elif missing:
        risk = "medium"
    else:
        risk = "low"
    return {
        "commit_sha": artifact["commit_sha"],
        "version": artifact["version"],
        "target": target,
        "ready": not missing,
        "approved": False,  # a human decides; a report never does
        "missing": missing,
        "risk": risk,
        "checks": checks,
        "stories": [s["key"] for s in stories],
        "stories_detail": stories,
        "evaluated_at": _now(),
    }


def notes(*, version: str, commit_sha: str, stories: list[dict[str, Any]], built_at: str) -> str:
    """Versioned notes derived from the linked issues, never hand-written prose."""
    version = check_version(version)
    commit_sha = check_commit(commit_sha)
    stories = check_stories(stories)
    features = [s for s in stories if s["type"].lower() in {"story", "feature", "epic"}]
    fixes = [s for s in stories if s["type"].lower() in {"bug", "task", "subtask"}]
    lines = [f"# Release {version}", "", f"- commit: `{commit_sha}`", f"- built: {built_at}", ""]
    for title, group in (("Features", features), ("Fixes and tasks", fixes)):
        if not group:
            continue
        lines.append(f"## {title}")
        lines.extend(f"- {s['key']} — {s['summary'] or '(no summary)'}" for s in group)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def verify_notes(text: str, artifact: dict[str, Any]) -> list[str]:
    """Notes are accepted only if they describe exactly the artifact's stories."""
    mentioned = set(re.findall(r"SCRUM-\d+", text or ""))
    expected = set(artifact.get("stories", []))
    missing = sorted(expected - mentioned)
    extra = sorted(mentioned - expected)
    if missing or extra:
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if extra:
            detail.append("not in the artifact: " + ", ".join(extra))
        raise ValueError("notes do not match the artifact (" + "; ".join(detail) + ")")
    return sorted(mentioned)


def rollback_plan(*, current: dict[str, Any], previous: dict[str, Any] | None, schema_compatible: bool) -> dict[str, Any]:
    """Select a known prior release; refusing is the default."""
    if previous is None:
        raise ValueError("no previous release to roll back to")
    if previous["id"] == current["id"]:
        raise ValueError("a release cannot roll back to itself")
    if previous["commit_sha"] == current["commit_sha"]:
        raise ValueError("the previous release holds the same commit: a rollback would change nothing")
    if previous["status"] not in ("published", "rolled_back"):
        raise ValueError(f"target release is {previous['status']!r}, not a published artifact")
    if not schema_compatible:
        raise ValueError("rollback refused: schema compatibility was not confirmed for this pair")
    return {
        "from_release": current["id"],
        "from_version": current["version"],
        "to_release": previous["id"],
        "to_version": previous["version"],
        "to_commit": previous["commit_sha"],
        "schema_compatible": True,
        "requires_human": True,
    }
