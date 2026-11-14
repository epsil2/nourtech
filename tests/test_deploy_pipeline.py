"""Policy tests for the GitHub Actions release chain (SCRUM-21).

DEV -> Qualite -> Production, all on GitHub Actions. The properties below are
asserted rather than documented, because this file is the only path to production.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / ".github" / "workflows" / "deploy.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"
WATCHER = ROOT / "scripts" / "release_watcher.sh"
SERVICE = ROOT / "deploy" / "systemd" / "factory-release-watcher.service"
TIMER = ROOT / "deploy" / "systemd" / "factory-release-watcher.timer"


def deploy_text() -> str:
    return DEPLOY.read_text(encoding="utf-8")


def jobs() -> dict[str, str]:
    text = deploy_text()
    out = {}
    for name in ("dev", "qualite", "production"):
        assert re.search(rf"^  {name}:", text, re.MULTILINE), name
        out[name] = text.split(f"  {name}:", 1)[1]
    return out


def test_pipeline_files_exist():
    for path in (DEPLOY, CI, WATCHER, SERVICE, TIMER):
        assert path.is_file(), path


def test_chain_is_dev_then_quality_then_production():
    text = deploy_text()
    assert "needs: dev" in jobs()["qualite"].split("  qualite:", 1)[0]
    assert "needs: qualite" in jobs()["production"]
    # the DEV stage boots the stack for real and probes both endpoints
    dev = jobs()["dev"]
    assert "docker compose up -d --wait" in dev
    assert "/healthz" in dev and "/readyz" in dev
    assert "docker compose down -v" in dev


def test_production_is_manual_and_main_only():
    prod = jobs()["production"]
    assert "workflow_dispatch:" in deploy_text()
    assert "confirm:" in deploy_text()
    assert "inputs.confirm == 'deploy'" in prod
    assert "github.ref == 'refs/heads/main'" in prod
    assert re.search(r"^    environment:\n      name: production", prod, re.MULTILINE)
    assert "concurrency:" in deploy_text()
    # a merge alone must never reach production
    assert not re.search(r"^  push:", deploy_text(), re.MULTILINE)


def test_quality_job_runs_tests_and_stack_checks():
    qualite = jobs()["qualite"]
    assert "pytest" in qualite
    assert "cp .env.example .env" in qualite
    assert "cd ../prod" in qualite
    assert "secret scan: ok" in qualite


def test_production_publishes_tag_and_waits_for_verified_result():
    prod = jobs()["production"]
    assert 'git tag -f "release/$SHA"' in prod
    assert 'git push origin "refs/tags/release/$SHA"' in prod
    assert "commit" in prod and "/statuses" in prod
    assert 'select(.context == "factory/production")' in prod
    assert "exit 1" in prod  # a failed deployment fails the run
    # the runner never gets shell access to the VPS
    assert "ssh " not in prod
    assert "DEPLOY_SSH_KEY" not in prod
    assert "contents: write" in prod


def test_watcher_only_deploys_main_and_reports_status():
    text = WATCHER.read_text(encoding="utf-8")
    assert "release/*" in text
    assert "compare/main..." in text
    assert "deploy_prod.sh" in text
    assert "FACTORY_IMAGE_TAG=\"$ORDERED_SHA\"" in text
    assert 'post_status "$ORDERED_SHA" "success"' in text
    assert 'post_status "$ORDERED_SHA" "failure"' in text
    assert "deployed-tags" in text and "failed-tags" in text
    assert "rsync -a --delete" in text


def test_watcher_carries_no_credential():
    patterns = (
        r"glpat-[A-Za-z0-9_-]{20,}",
        r"gh[pousr]_[A-Za-z0-9]{20,}",
        r"github_pat_[A-Za-z0-9_]{20,}",
        r"ATATT[A-Za-z0-9_=-]{10,}",
        r"-----BEGIN OPENSSH PRIVATE KEY-----",
    )
    for path in (DEPLOY, CI, WATCHER):
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            assert not re.search(pattern, text), f"{pattern} in {path.name}"


def test_timer_is_enabled_periodically():
    assert "OnUnitActiveSec=60" in TIMER.read_text(encoding="utf-8")
    assert "release_watcher.sh" in SERVICE.read_text(encoding="utf-8")


def test_gitlab_path_is_removed():
    assert not (ROOT / ".gitlab-ci.yml").exists()
    assert not (ROOT / ".github" / "workflows" / "mirror-to-gitlab.yml").exists()
