PY ?= python3
VENV ?= .venv

.PHONY: venv test compose-check audit dev-up dev-down dev-ps prod-check prod-dry prod-deploy prod-ps prod-logs prod-down prod-smoke pipeline-check

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

.PHONY: prod-check prod-dry prod-deploy prod-ps prod-logs prod-down prod-smoke pipeline-check

prod-check:
	cd deploy/prod && FACTORY_IMAGE_TAG=check docker compose config --quiet && echo "prod compose valid"

prod-dry:
	FACTORY_DEPLOY_DRY_RUN=1 FACTORY_IMAGE_TAG=$$(git rev-parse --short HEAD) ./scripts/deploy_prod.sh

prod-deploy:
	FACTORY_IMAGE_TAG=$$(git rev-parse HEAD) ./scripts/deploy_prod.sh

prod-ps:
	cd deploy/prod && docker compose ps

prod-logs:
	cd deploy/prod && docker compose logs --tail 50

prod-down:
	cd deploy/prod && docker compose down

prod-smoke:
	curl -fsS http://127.0.0.1:8081/healthz && echo && curl -fsS http://127.0.0.1:8081/readyz && echo

# Validation locale du pipeline de release (mêmes gates que le job qualite)
pipeline-check:
	$(VENV)/bin/python -m pytest -q tests/test_deploy_pipeline.py tests/test_prod_policy.py
	cd deploy/dev && cp -n .env.example .env 2>/dev/null || true; docker compose config --quiet && echo "dev compose valid"
	cd deploy/prod && cp -n .env.example .env 2>/dev/null || true; FACTORY_IMAGE_TAG=check docker compose config --quiet && echo "prod compose valid"
