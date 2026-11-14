"""Policy tests for the GitHub Actions release pipeline (SCRUM-21).

The pipeline is the only path to production, so the properties below are asserted
rather than documented: quality gates before production, a manual confirmation,
a protected environment, main-only, and no credential in the repository.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / ".github" / "workflows" / "deploy.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"
KNOWN_HOSTS = ROOT / "deploy" / "prod" / "known_hosts"
RECEIVER = Path("/opt/agent-factory/bin/receive.sh")


def deploy_text() -> str:
    return DEPLOY.read_text(encoding="utf-8")


def test_pipeline_file_exists():
    assert DEPLOY.is_file()
    assert CI.is_file()


def test_production_is_manual_and_main_only():
    text = deploy_text()
    assert "workflow_dispatch:" in text
    assert "confirm:" in text
    assert "inputs.confirm == 'deploy'" in text
    # no automatic push trigger: a merge must never reach production by itself
    assert not re.search(r"^  push:", text, re.MULTILINE)


def test_chain_is_dev_then_quality_then_production():
    text = deploy_text()
    jobs = {}
    for name in ("dev", "qualite", "production"):
        assert re.search(rf"^  {name}:", text, re.MULTILINE), name
        jobs[name] = text.split(f"  {name}:", 1)[1]
    assert "needs: dev" in jobs["qualite"]
    assert "needs: qualite" in jobs["production"]
    # the DEV stage really boots the stack and calls both probes
    dev = jobs["qualite"].split("  qualite:", 1)[0] if False else jobs["dev"].split("  qualite:", 1)[0]
    assert "docker compose up -d --wait" in dev
    assert "/healthz" in dev and "/readyz" in dev
    assert "docker compose down -v" in dev


def test_production_requires_quality_gate_and_environment():
    text = deploy_text()
    assert re.search(r"^  qualite:", text, re.MULTILINE)
    prod = text.split("  production:", 1)[1]
    assert "needs: qualite" in prod
    assert "environment:" in prod
    assert re.search(r"name: production", prod)
    assert "github.ref == 'refs/heads/main'" in prod
    assert "concurrency:" in text


def test_quality_job_runs_tests_and_stack_checks():
    qualite = deploy_text().split("  qualite:", 1)[1].split("  production:", 1)[0]
    assert "pytest" in qualite
    assert "cp .env.example .env" in qualite
    assert "cd ../prod" in qualite
    assert "secret scan: ok" in qualite


def test_deploy_uses_archive_and_restricted_ssh_command():
    prod = deploy_text().split("  production:", 1)[1]
    assert "git archive --format=tar HEAD" in prod
    assert 'deploy $SHA' in prod
    assert "IdentitiesOnly=yes" in prod
    assert "BatchMode=yes" in prod
    assert "DEPLOY_SSH_KEY" in prod
    # the key is only accepted through a restricted forced command.
    # The receiver lives on the VPS, so its content is only checked there (CI
    # runners have no /opt/agent-factory) but the contract is checked everywhere.
    if RECEIVER.is_file():
        recv = RECEIVER.read_text(encoding="utf-8")
        assert "SSH_ORIGINAL_COMMAND" in recv
        assert "^[0-9a-f]{40}$" in recv
        assert "rsync -a --delete" in recv
        assert "deploy_prod.sh" in recv


def test_host_key_is_pinned_in_the_repository():
    hosts = KNOWN_HOSTS.read_text(encoding="utf-8").strip()
    assert hosts.startswith("2.24.51.199 ssh-ed25519 ")
    assert len(hosts.split()) == 3


def test_pipeline_carries_no_credentials():
    patterns = (
        r"glpat-[A-Za-z0-9_-]{20,}",
        r"gh[pousr]_[A-Za-z0-9]{20,}",
        r"github_pat_[A-Za-z0-9_]{20,}",
        r"ATATT[A-Za-z0-9_=-]{10,}",
        r"-----BEGIN OPENSSH PRIVATE KEY-----",
    )
    for path in (DEPLOY, CI, KNOWN_HOSTS):
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            assert not re.search(pattern, text), f"{pattern} in {path.name}"


def test_gitlab_path_is_removed():
    assert not (ROOT / ".gitlab-ci.yml").exists()
    assert not (ROOT / ".github" / "workflows" / "mirror-to-gitlab.yml").exists()
