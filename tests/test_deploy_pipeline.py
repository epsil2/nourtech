"""Policy tests for the release chain (SCRUM-21): DEV -> Qualite -> Production.

Automatic: `deploy.yml` on every merge to main (DEV, then quality gates).
Deliberate: `release.yml` only on a human-published `release/<sha>` tag, which the
VPS watcher turns into a real deployment and a verified commit status.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / ".github" / "workflows" / "deploy.yml"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"
WATCHER = ROOT / "scripts" / "release_watcher.sh"
SERVICE = ROOT / "deploy" / "systemd" / "factory-release-watcher.service"
TIMER = ROOT / "deploy" / "systemd" / "factory-release-watcher.timer"


def job(text: str, name: str) -> str:
    assert re.search(rf"^  {name}:", text, re.MULTILINE), f"missing job {name}"
    return text.split(f"  {name}:", 1)[1]


def test_pipeline_files_exist():
    for path in (DEPLOY, RELEASE, CI, WATCHER, SERVICE, TIMER):
        assert path.is_file(), path


def test_dev_stage_runs_on_merge_and_boots_the_real_stack():
    text = DEPLOY.read_text(encoding="utf-8")
    assert re.search(r"^  push:\n    branches: \[main\]", text, re.MULTILINE)
    dev = job(text, "dev")
    assert "docker build" in dev
    assert "docker compose up -d --wait" in dev
    assert "/healthz" in dev and "/readyz" in dev
    assert "docker compose down -v" in dev
    assert "if: always()" in dev  # teardown even when the smoke test fails


def test_quality_gate_follows_dev():
    text = DEPLOY.read_text(encoding="utf-8")
    qualite = job(text, "qualite")
    assert "needs: dev" in qualite
    assert "pytest" in qualite
    assert "cd ../prod" in qualite
    assert "secret scan: ok" in qualite


def test_production_needs_a_human_tag_not_a_merge():
    text = RELEASE.read_text(encoding="utf-8")
    assert 'push:\n    tags:\n      - "release/*"' in text
    # no branch push and no manual dispatch: the tag is the only trigger
    assert "branches:" not in text
    assert "workflow_dispatch" not in text
    prod = job(text, "production")
    assert "merge-base --is-ancestor" in prod
    assert 'select(.context == "factory/production")' in prod
    assert "exit 1" in prod
    assert "concurrency:" in text
    assert re.search(r"^    environment:\n      name: production", prod, re.MULTILINE)


def test_runner_never_gets_host_access():
    prod = job(RELEASE.read_text(encoding="utf-8"), "production")
    for forbidden in ("ssh ", "DEPLOY_SSH_KEY", "docker run", "docker compose up"):
        assert forbidden not in prod, forbidden


def test_watcher_only_deploys_main_and_reports_status():
    text = WATCHER.read_text(encoding="utf-8")
    assert "release/*" in text
    assert "compare/main..." in text
    assert "deploy_prod.sh" in text
    assert 'FACTORY_IMAGE_TAG="$ORDERED_SHA"' in text
    assert 'post_status "$ORDERED_SHA" "success"' in text
    assert 'post_status "$ORDERED_SHA" "failure"' in text
    assert "deployed-tags" in text and "failed-tags" in text
    assert "rsync -a --delete" in text
    assert "--exclude 'deploy/prod/.env'" in text


def test_timer_runs_every_minute():
    assert "OnUnitActiveSec=60" in TIMER.read_text(encoding="utf-8")
    assert "release_watcher.sh" in SERVICE.read_text(encoding="utf-8")


def test_pipeline_carries_no_credential():
    patterns = (
        r"glpat-[A-Za-z0-9_-]{20,}",
        r"gh[pousr]_[A-Za-z0-9]{20,}",
        r"github_pat_[A-Za-z0-9_]{20,}",
        r"ATATT[A-Za-z0-9_=-]{10,}",
        r"-----BEGIN OPENSSH PRIVATE KEY-----",
    )
    for path in (DEPLOY, RELEASE, CI, WATCHER):
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            assert not re.search(pattern, text), f"{pattern} in {path.name}"


def test_gitlab_path_is_removed():
    assert not (ROOT / ".gitlab-ci.yml").exists()
    assert not (ROOT / ".github" / "workflows" / "mirror-to-gitlab.yml").exists()
