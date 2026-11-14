#!/usr/bin/env bash
# Production deployment of the AI Agent Factory stack (SCRUM-21).
#
# Called by the GitLab "production" stage job (manual, main only) and runnable by
# an operator on the VPS. It never touches another Compose project: every docker
# command is scoped with -f deploy/prod/docker-compose.yml and the project name
# defined inside it (agent-factory-prod).
#
# Required: deploy/prod/.env (chmod 600) with FACTORY_DB_PASSWORD, and
# FACTORY_IMAGE_TAG exported by the caller (the commit sha being deployed).
# On failure the previous known-good image tag is redeployed, else the stack is
# stopped; the exit code is non-zero either way.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STACK_DIR="$ROOT/deploy/prod"
COMPOSE_FILE="$STACK_DIR/docker-compose.yml"
ENV_FILE="$STACK_DIR/.env"
# Optional secrets store kept OUTSIDE the repo: the tree must stay credential-free
# (tests/test_deploy_pipeline.py scans it), so integration tokens live here.
SECRETS_FILE="${FACTORY_SECRETS_FILE:-/opt/agent-factory/secrets/jira.env}"
STATE_DIR="${FACTORY_STATE_DIR:-/opt/agent-factory/state}"
BACKUP_DIR="${FACTORY_BACKUP_DIR:-/opt/agent-factory/backups}"
HEALTH_URL="http://127.0.0.1:8081"
MIN_MEM_MIB="${FACTORY_MIN_MEM_MIB:-700}"
MIN_DISK_GIB="${FACTORY_MIN_DISK_GIB:-3}"
DRY_RUN="${FACTORY_DEPLOY_DRY_RUN:-0}"

log()  { printf '[deploy] %s\n' "$*"; }
fail() { printf '[deploy] ERROR: %s\n' "$*" >&2; exit 1; }

compose() {
  if [ -f "$SECRETS_FILE" ]; then
    docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" --env-file "$SECRETS_FILE" "$@"
  else
    docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"
  fi
}

# --- 0. inputs -------------------------------------------------------------
TAG="${FACTORY_IMAGE_TAG:-}"
[ -n "$TAG" ] || fail "FACTORY_IMAGE_TAG is not set (expected the git commit sha)"
[ -f "$ENV_FILE" ] || fail "$ENV_FILE is missing: create it from deploy/prod/.env.example and chmod 600"
chmod 600 "$ENV_FILE" 2>/dev/null || true
grep -q '^FACTORY_DB_PASSWORD=change-me-production$' "$ENV_FILE" && fail "FACTORY_DB_PASSWORD is still the placeholder in $ENV_FILE"
command -v docker >/dev/null || fail "docker is not installed"
docker compose version >/dev/null 2>&1 || fail "docker compose plugin is missing"

# --- 1. preflight capacity (the host is shared with production services) ----
mem_avail="$(awk '/^MemAvailable:/ {print int($2/1024)}' /proc/meminfo)"
disk_avail_gib="$(df -Pk / | awk 'NR==2 {print int($4/1048576)}')"
log "preflight: ${mem_avail} MiB RAM available, ${disk_avail_gib} GiB free on /"
[ "$mem_avail" -ge "$MIN_MEM_MIB" ] || fail "not enough RAM: ${mem_avail} MiB < ${MIN_MEM_MIB} MiB (stack needs ~608 MiB)"
[ "$disk_avail_gib" -ge "$MIN_DISK_GIB" ] || fail "not enough disk: ${disk_avail_gib} GiB < ${MIN_DISK_GIB} GiB"

# --- 2. compose validity ---------------------------------------------------
FACTORY_IMAGE_TAG="$TAG" compose config --quiet || fail "compose file is invalid"
log "compose config: ok (project agent-factory-prod, tag ${TAG})"

if [ "$DRY_RUN" = "1" ]; then
  log "dry run: preflight and compose validation passed, nothing started"
  exit 0
fi

# --- 3. state, backup, rollback bookkeeping --------------------------------
mkdir -p "$STATE_DIR" "$BACKUP_DIR"
PREV_TAG=""
[ -f "$STATE_DIR/last-good" ] && PREV_TAG="$(cat "$STATE_DIR/last-good")"
log "previous known-good tag: ${PREV_TAG:-none}"

backup_db() {
  if compose ps db 2>/dev/null | grep -q ' Up '; then
    local out="$BACKUP_DIR/prod-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
    if compose exec -T db pg_dumpall -U "${FACTORY_DB_USER:-factory}" | gzip > "$out"; then
      log "database backup: $out ($(du -h "$out" | cut -f1))"
      ls -1t "$BACKUP_DIR"/prod-*.sql.gz 2>/dev/null | tail -n +6 | xargs -r rm -f
    else
      log "database backup: skipped (dump failed, stack not running yet?)"
      rm -f "$out"
    fi
  fi
}

rollback() {
  log "deployment failed, rolling back"
  compose logs --tail 40 api || true
  if [ -n "$PREV_TAG" ] && [ "$PREV_TAG" != "$TAG" ]; then
    log "rolling back to ${PREV_TAG}"
    if FACTORY_IMAGE_TAG="$PREV_TAG" compose up -d --wait --wait-timeout 180; then
      log "rollback complete: ${PREV_TAG} is serving"
      return 0
    fi
    log "rollback to ${PREV_TAG} failed too"
  fi
  log "no usable previous image: stopping the prod stack"
  compose down
  return 1
}

# A failure raised by fail() is an explicit `exit`, which does NOT fire an ERR
# trap: the cleanup must therefore hang off EXIT.
cleanup() {
  local rc=$?
  trap - EXIT
  if [ "$rc" -ne 0 ]; then
    log "deployment failed (rc=$rc)"
    if [ "${STACK_TOUCHED:-0}" = "1" ]; then
      rollback || true
    else
      log "stack not touched: nothing to roll back"
    fi
  fi
  exit "$rc"
}
trap cleanup EXIT

# --- 4. build, start, smoke ------------------------------------------------
backup_db

log "building api image agent-factory-api:${TAG}"
docker build -t "agent-factory-api:${TAG}" "$ROOT/services/api" >/dev/null

STACK_TOUCHED=1
log "starting the stack"
FACTORY_IMAGE_TAG="$TAG" compose up -d --wait --wait-timeout 240

for i in $(seq 1 20); do
  if curl -fsS --max-time 3 "$HEALTH_URL/readyz" 2>/dev/null | grep -q '"status":"ready"'; then
    break
  fi
  [ "$i" -eq 20 ] && fail "smoke test failed: $HEALTH_URL/readyz not ready after 20 attempts"
  sleep 3
done

curl -fsS --max-time 3 "$HEALTH_URL/healthz" | grep -q '"status":"ok"' || fail "smoke test failed: $HEALTH_URL/healthz"

printf '%s\n' "$TAG" > "$STATE_DIR/last-good"
printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$TAG" >> "$STATE_DIR/deploy.log"

log "deployed ${TAG}: /healthz and /readyz are green on ${HEALTH_URL} (loopback only)"
compose ps
