PY ?= python3
VENV ?= .venv

.PHONY: venv test compose-check audit dev-up dev-down dev-ps

venv:
	$(PY) -m venv $(VENV)
	$(VENV)/bin/pip install -q -r services/api/requirements-dev.txt

test:
	$(VENV)/bin/python -m pytest -q

compose-check:
	cd deploy/dev && docker compose config --quiet && echo "compose config valid"

audit:
	$(PY) scripts/vps_audit.py --json docs/baseline/baseline.json --markdown docs/baseline/baseline.md

dev-up:
	cd deploy/dev && docker compose up -d --wait

dev-ps:
	cd deploy/dev && docker compose ps

dev-down:
	cd deploy/dev && docker compose down
