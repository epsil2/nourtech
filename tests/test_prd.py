"""PRD generation and approval (SCRUM-25, SCRUM-34, SCRUM-35)."""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))
os.environ["FACTORY_STORE"] = "memory"

from app import prd as prd_generator  # noqa: E402

REQUEST = (
    "Créer un agent qui prépare une release hebdomadaire. "
    "Il ne doit jamais écrire dans Jira. "
    "Les secrets doivent rester hors du dépôt."
)


@pytest.fixture()
def client():
    main = importlib.import_module("app.main")
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def test_generation_is_deterministic():
    a = prd_generator.generate(REQUEST, "SCRUM-25")
    b = prd_generator.generate(REQUEST, "SCRUM-25")
    a.pop("generated_at"), b.pop("generated_at")
    assert a == b


def test_prd_has_the_sections_a_reviewer_needs():
    doc = prd_generator.generate(REQUEST, "SCRUM-25")
    sections = doc["sections"]
    assert set(sections) == {
        "contexte",
        "objectif",
        "perimetre",
        "hors_perimetre",
        "contraintes",
        "risques",
        "critères_d_acceptation",
    }
    assert all(sections.values()), "no section may be empty"
    assert sections["hors_perimetre"], "out of scope is a decision, not an omission"
    assert any("Jira" in c for c in sections["contraintes"])
    assert doc["traceability"]["source"] == "jira"
    assert doc["traceability"]["source_key"] == "SCRUM-25"


def test_short_request_is_refused():
    with pytest.raises(ValueError):
        prd_generator.generate("trop court")


def test_markdown_rendering_contains_decisions():
    md = prd_generator.render_markdown(prd_generator.generate(REQUEST, "SCRUM-25"))
    assert md.startswith("# PRD")
    assert "Décisions de conception" in md
    assert "SCRUM-25" in md


def test_api_creates_reads_and_approves_a_prd(client):
    created = client.post("/api/v1/prd", json={"request": REQUEST, "source_key": "SCRUM-25"})
    assert created.status_code == 201, created.text
    row = created.json()
    assert row["status"] == "draft"
    assert row["payload"]["traceability"]["source_key"] == "SCRUM-25"

    assert client.get(f"/api/v1/prd/{row['id']}").json()["id"] == row["id"]
    assert client.get("/api/v1/prd").json()["count"] == 1
    md = client.get(f"/api/v1/prd/{row['id']}/markdown")
    assert md.status_code == 200 and md.text.startswith("# PRD")

    approved = client.post(f"/api/v1/prd/{row['id']}/approve", headers={"X-Actor": "soufiane"})
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    # approving twice is idempotent, not an error
    assert client.post(f"/api/v1/prd/{row['id']}/approve").json()["status"] == "approved"

    audit = client.get("/api/v1/audit").json()["items"]
    assert [a["action"] for a in audit if a["entity_type"] == "prd"] == [
        "prd.approved",
        "prd.created",
    ]
    assert client.get("/api/v1/prd/unknown").status_code == 404


def test_bad_source_key_is_rejected(client):
    bad = client.post("/api/v1/prd", json={"request": REQUEST, "source_key": "not-a-key"})
    assert bad.status_code == 422
