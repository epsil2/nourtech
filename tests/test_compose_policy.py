"""Security and isolation policy tests for deploy/dev/docker-compose.yml (SCRUM-19).

These are the "unit" and "security scan" gates of the story: they fail the build
if the DEV stack could disturb an existing service, publish a database port, or
escape its resource ceiling.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parents[1] / "deploy" / "dev" / "docker-compose.yml"
EXPECTED_PROJECT = "agent-factory-dev"
RESERVED_PROJECTS = {"traefik", "hermes-web", "nourredine-site", "trading-platform", "vibe-trading", "video2tutorial"}


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def test_project_name_is_factory_specific(compose):
    assert compose["name"] == EXPECTED_PROJECT
    assert compose["name"] not in RESERVED_PROJECTS


def test_no_service_publishes_a_port(compose):
    offenders = [name for name, svc in compose["services"].items() if svc.get("ports")]
    assert offenders == [], f"services publishing host ports: {offenders}"


def test_no_host_network_and_no_privileged(compose):
    for name, svc in compose["services"].items():
        assert svc.get("network_mode") != "host", name
        assert svc.get("privileged") is not True, name
        assert "no-new-privileges:true" in (svc.get("security_opt") or []), name


def test_no_docker_socket_mounted(compose):
    for name, svc in compose["services"].items():
        for volume in svc.get("volumes") or []:
            assert "/var/run/docker.sock" not in str(volume), name


def test_every_service_is_on_the_internal_network_only(compose):
    networks = compose["networks"]
    assert networks["factory-internal"]["internal"] is True
    for name, svc in compose["services"].items():
        assert svc["networks"] == ["factory-internal"], name


def test_database_and_cache_have_no_declared_ports(compose):
    for svc_name, port in (("db", "5432"), ("cache", "6379")):
        svc = compose["services"][svc_name]
        assert not svc.get("ports"), svc_name
        assert port not in [str(p).split(":")[-1] for p in svc.get("ports") or []]


def test_every_service_has_healthcheck_and_resource_limits(compose):
    for name, svc in compose["services"].items():
        assert svc.get("healthcheck", {}).get("test"), f"{name}: missing healthcheck"
        limits = svc.get("deploy", {}).get("resources", {}).get("limits", {})
        assert limits.get("memory"), f"{name}: missing memory limit"
        assert limits.get("cpus"), f"{name}: missing cpu limit"
        memory_mib = int(str(limits["memory"]).rstrip("MmGg").rstrip("iB")) if str(limits["memory"]).upper().endswith("M") else None
        assert memory_mib and memory_mib <= 512, f"{name}: memory limit too high for this host"
    total_limit = sum(
        int(str(svc["deploy"]["resources"]["limits"]["memory"]).rstrip("M")) for svc in compose["services"].values()
    )
    # Host baseline: 3915 MiB total with ~1795 MiB available.
    assert total_limit <= 1024, f"sum of memory ceilings too high: {total_limit} MiB"


def test_volume_is_factory_owned(compose):
    assert compose["volumes"]["factory-db-data"]["name"] == "agent-factory-dev-db-data"


def test_secrets_are_not_inlined_in_compose(compose):
    raw = COMPOSE.read_text()
    for svc in compose["services"].values():
        env = svc.get("environment") or {}
        if isinstance(env, dict):
            for key, value in env.items():
                if "PASSWORD" in key.upper() or "SECRET" in key.upper() or "TOKEN" in key.upper():
                    assert "${" in str(value), f"{key} must come from the environment, not the file"
    assert "ATATT" not in raw and "github_pat_" not in raw


def test_log_rotation_is_bounded(compose):
    for name, svc in compose["services"].items():
        logging_cfg = svc.get("logging") or {}
        assert logging_cfg.get("options", {}).get("max-size"), f"{name}: unbounded logs"
