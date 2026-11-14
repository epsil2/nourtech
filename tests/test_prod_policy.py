"""Production stack policy (SCRUM-21).

Guards the differences that make deploy/prod safe to run next to the existing
production services: loopback-only exposure, immutable image tags, restart
policy, hardening, and a memory budget measured against the host baseline.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PROD = ROOT / "deploy" / "prod" / "docker-compose.yml"
DEV = ROOT / "deploy" / "dev" / "docker-compose.yml"

EXPECTED_PROJECT = "agent-factory-prod"
RESERVED_PROJECTS = {
    "traefik",
    "hermes-web",
    "nourredine-site",
    "trading-platform",
    "vibe-trading",
    "video2tutorial",
    "agent-factory-dev",
}
HOST_RAM_MIB = 3915  # docs/baseline/vps-baseline-2026-11-14.md
MAX_STACK_MIB = 700


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(PROD.read_text())


def test_project_name_is_factory_specific(compose):
    assert compose["name"] == EXPECTED_PROJECT
    assert compose["name"] not in RESERVED_PROJECTS


def test_api_is_exposed_on_loopback_only(compose):
    ports = compose["services"]["api"].get("ports") or []
    assert ports == ["127.0.0.1:8081:8080"], ports


def test_no_other_service_publishes_a_port(compose):
    for name, svc in compose["services"].items():
        if name == "api":
            continue
        assert not svc.get("ports"), f"{name} publishes {svc['ports']}"


def test_network_is_internal(compose):
    networks = compose["networks"]
    assert list(networks) == ["factory-internal"]
    assert networks["factory-internal"]["internal"] is True
    assert networks["factory-internal"]["name"] == "agent-factory-prod-internal"


def test_every_service_restarts_and_is_hardened(compose):
    for name, svc in compose["services"].items():
        assert svc.get("restart") == "always", name
        assert "no-new-privileges:true" in (svc.get("security_opt") or []), name


def test_every_service_has_a_healthcheck(compose):
    for name, svc in compose["services"].items():
        assert svc.get("healthcheck", {}).get("test"), name


def test_memory_budget_fits_the_host(compose):
    total = 0
    for name, svc in compose["services"].items():
        limits = svc.get("deploy", {}).get("resources", {}).get("limits", {})
        assert "memory" in limits, name
        assert "cpus" in limits, name
        total += int(limits["memory"].rstrip("Mm"))
    assert total <= MAX_STACK_MIB, total
    assert total < HOST_RAM_MIB * 0.20, "prod stack must stay under 20% of host RAM"


def test_api_image_tag_is_pinned_not_latest(compose):
    image = compose["services"]["api"]["image"]
    assert "FACTORY_IMAGE_TAG" in image
    assert ":latest" not in image
    assert image.startswith("agent-factory-api:")


def test_api_runs_in_prod_mode(compose):
    env = compose["services"]["api"]["environment"]
    assert env["FACTORY_ENV"] == "prod"
    assert env["FACTORY_DB_HOST"] == "db"
    assert env["FACTORY_CACHE_HOST"] == "cache"


def test_database_password_has_no_default(compose):
    raw = compose["services"]["db"]["environment"]["POSTGRES_PASSWORD"]
    assert ":?" in raw, "the password must be required, not defaulted"
    assert ":-" not in raw, f"a fallback default weakens the deploy: {raw}"


def test_prod_uses_its_own_volume(compose):
    assert compose["volumes"]["factory-db-data"]["name"] == "agent-factory-prod-db-data"
    dev = yaml.safe_load(DEV.read_text())
    assert dev["volumes"]["factory-db-data"]["name"] != compose["volumes"]["factory-db-data"]["name"]


def test_env_example_carries_no_real_secret():
    example = (ROOT / "deploy" / "prod" / ".env.example").read_text()
    assert "change-me-production" in example
    for line in example.splitlines():
        if line.startswith("FACTORY_DB_PASSWORD="):
            value = line.split("=", 1)[1]
            assert len(value) < 40, "example must not contain a generated password"
