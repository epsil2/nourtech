"""GitLab delivery pipeline policy (SCRUM-21).

The pipeline must gate production: quality jobs always run, the production job is
manual, default-branch only, serialised, and dependent on every quality job.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".gitlab-ci.yml"
QUALITY_JOBS = {"qualite:tests", "qualite:compose", "qualite:secrets"}


@pytest.fixture(scope="module")
def ci() -> dict:
    return yaml.safe_load(CI.read_text())


def test_stage_order_is_quality_then_production(ci):
    assert ci["stages"] == ["qualite", "production"]


def test_quality_jobs_run_on_every_merge_request_and_default_branch(ci):
    for job in QUALITY_JOBS:
        rules = ci[job]["rules"]
        assert any('merge_request_event' in str(r.get("if", "")) for r in rules), job
        assert any("CI_DEFAULT_BRANCH" in str(r.get("if", "")) for r in rules), job
        assert ci[job]["stage"] == "qualite"


def test_production_job_is_manual_and_default_branch_only(ci):
    job = ci["production:deploy"]
    assert job["stage"] == "production"
    rules = job["rules"]
    assert len(rules) == 1
    assert "CI_DEFAULT_BRANCH" in rules[0]["if"]
    assert rules[0]["when"] == "manual"
    assert rules[0].get("allow_failure") is False, "a manual gate must not be skippable"


def test_production_depends_on_every_quality_job(ci):
    needs = set(ci["production:deploy"]["needs"])
    assert needs == QUALITY_JOBS


def test_production_is_serialised_and_environment_scoped(ci):
    job = ci["production:deploy"]
    assert job["resource_group"] == "production"
    assert job["environment"]["name"] == "production"


def test_pipeline_carries_no_credentials(ci):
    """The file may *describe* token shapes (the secret scan) but never hold one."""
    text = CI.read_text()
    shapes = {
        "gitlab_pat": r"glpat-[A-Za-z0-9_-]{20,}",
        "github_classic": r"gh[pousr]_[A-Za-z0-9]{20,}",
        "github_fine_grained": r"github_pat_[A-Za-z0-9_]{20,}",
        "jira": r"ATATT[A-Za-z0-9_=-]{10,}",
        "private_key": r"-----BEGIN OPENSSH PRIVATE KEY-----",
    }
    import re
    for name, pattern in shapes.items():
        assert not re.search(pattern, text), name
    job = ci["production:deploy"]
    assert "$DEPLOY_HOST" in " ".join(job["script"])
    assert "$CI_COMMIT_SHA" in " ".join(job["script"])
