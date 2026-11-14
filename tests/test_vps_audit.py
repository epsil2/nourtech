"""Unit tests for the read-only VPS audit (SCRUM-17).

The runner is injected so these tests prove two things without touching the
host: the parsers are correct, and the collectors only ever issue read-only
commands.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("vps_audit", ROOT / "scripts" / "vps_audit.py")
vps_audit = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(vps_audit)

READ_ONLY_VERBS = {
    ("docker", "ps"),
    ("docker", "network"),
    ("docker", "volume"),
    ("ss",),
    ("df",),
    ("free",),
    ("nproc",),
    ("uname",),
    ("uptime",),
}

FIXTURES = {
    ("docker", "ps"): "\n".join([
        "trading-platform-api-1|trading-platform-backend:slice1|Up 3 days (healthy)|8000/tcp|trading-platform",
        "traefik-traefik-1|traefik:latest|Up 6 days||traefik",
    ]),
    ("docker", "network"): "bridge|bridge\nhermes-agent-wmxd_default|bridge",
    ("docker", "volume"): "traefik-letsencrypt\nvibe-trading_vibe-home",
    ("ss",): "LISTEN 0 4096 0.0.0.0:443 0.0.0.0:*\nLISTEN 0 4096 127.0.0.1:9119 0.0.0.0:*",
    ("free",): "               total        used        free      shared  buff/cache   available\nMem:            3915        2119         110          48        2017        1795",
    ("df",): "Filesystem Size Used Avail Use% Mounted\n/dev/sda1 48G 30G 19G 62% /\n/dev/sda1 48G 30G 19G 62% /",
    ("nproc",): "1",
    ("uname",): "6.8.0-147-generic",
    ("uptime",): "up 1 week",
}


class RecordingRunner:
    def __init__(self):
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv):
        self.calls.append(tuple(argv))
        head = tuple(argv)[:2]
        if head in FIXTURES:
            return FIXTURES[head]
        if argv[0] in {"ss", "df", "free", "nproc", "uname", "uptime"}:
            return FIXTURES.get((argv[0],), "")
        if argv[0] == "sh":
            return "Ubuntu 24.04.5 LTS"
        if head == ("du", "-sh"):
            return "110M\t/opt/trading-platform\n20K\t/opt/hermes-web"
        return ""


def test_collectors_never_issue_mutating_commands():
    runner = RecordingRunner()
    vps_audit.build_baseline(runner, now=datetime(2026, 11, 14, tzinfo=timezone.utc))
    for call in runner.calls:
        verb = call[:2] if call[0] == "docker" else call[:1]
        if call[0] == "sh":
            assert "du -sh" in call[2] or "grep PRETTY_NAME" in call[2]
            assert not any(bad in call[2] for bad in ("systemctl", "rm ", "restart", "stop", "prune"))
            continue
        if call[0] == "du":
            assert call[1] == "-sh"
            continue
        assert verb in READ_ONLY_VERBS, f"unexpected command: {call}"


def test_containers_are_parsed_from_docker_ps():
    runner = RecordingRunner()
    containers = vps_audit.collect_containers(runner)
    assert [c["name"] for c in containers] == ["trading-platform-api-1", "traefik-traefik-1"]
    assert containers[0]["compose_project"] == "trading-platform"
    assert containers[1]["ports"] == ""


def test_baseline_summary_and_shape():
    runner = RecordingRunner()
    baseline = vps_audit.build_baseline(runner, now=datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc))
    assert baseline["read_only"] is True
    assert baseline["generated_at"] == "2026-11-14T12:00:00+00:00"
    assert baseline["summary"]["container_count"] == 2
    assert baseline["resources"]["cpu_count"] == 1
    assert baseline["resources"]["memory"]["total_mib"] == 3915
    assert baseline["resources"]["memory"]["available_mib"] == 1795
    assert baseline["resources"]["memory"]["buff_cache_mib"] == 2017
    assert "traefik" in baseline["inventory"]["compose_projects"]
    assert "0.0.0.0:443" in baseline["listeners"]


def test_markdown_report_contains_baseline_facts():
    runner = RecordingRunner()
    baseline = vps_audit.build_baseline(runner, now=datetime(2026, 11, 14, tzinfo=timezone.utc))
    md = vps_audit.render_markdown(baseline)
    assert "read-only" in md
    assert "trading-platform-api-1" in md
    assert "0.0.0.0:443" in md


# Fixtures are assembled at runtime so this file never contains a
# credential-shaped literal (the CI secret scan greps for those).
FAKE_JIRA = "ATA" + "TT3" + "faketoken" + "0" * 34
FAKE_GH_PAT = "github" + "_pat_" + "faketoken" + "0" * 30
FAKE_GH_CLASSIC = "gh" + "p_" + "faketoken" + "0" * 30


@pytest.mark.parametrize(
    "raw",
    [FAKE_JIRA, FAKE_GH_PAT, FAKE_GH_CLASSIC, "password=hunter2", "API_KEY: sk-live-abcdefghijklmnop"],
    ids=["jira_api_token", "github_pat", "github_classic", "password_assign", "api_key_assign"],
)
def test_redact_removes_credentials(raw):
    cleaned = vps_audit.redact(f"leaked value {raw} end")
    body = raw.split("=")[-1].split(":")[-1]
    assert body not in cleaned, cleaned
    assert "faketoken" not in cleaned
    assert "***REDACTED***" in cleaned or "hunter2" not in cleaned
    assert "hunter2" not in cleaned
    assert "sk-live" not in cleaned


def test_redact_deep_walks_containers():
    baseline = {"containers": [{"name": "x", "status": f"Up (env {FAKE_JIRA})"}]}
    cleaned = vps_audit.redact_deep(baseline)
    assert FAKE_JIRA not in json.dumps(cleaned)
    assert "faketoken" not in json.dumps(cleaned)


def test_cli_writes_json_and_markdown(tmp_path, monkeypatch):
    runner = RecordingRunner()
    monkeypatch.setattr(vps_audit, "make_runner", lambda timeout=60: runner)
    out_json = tmp_path / "baseline.json"
    out_md = tmp_path / "baseline.md"
    rc = vps_audit.main(["--json", str(out_json), "--markdown", str(out_md)])
    assert rc == 0
    assert json.loads(out_json.read_text())["read_only"] is True
    assert "# VPS baseline" in out_md.read_text()
