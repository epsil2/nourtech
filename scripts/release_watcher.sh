#!/usr/bin/env bash
# Release watcher for the AI Agent Factory (SCRUM-21).
#
# The GitHub Actions job `production` publishes an immutable tag
# `release/<40-hex sha>` once the DEV and quality stages are green and a human has
# approved the deployment. This watcher, on the VPS, is what actually touches the
# host: it validates that the tagged commit is an ancestor of `main`, deploys it
# with scripts/deploy_prod.sh, and reports the outcome back to GitHub as a commit
# status on that sha, so the workflow can wait for a verified result instead of
# assuming success.
#
# No credential lives in GitHub: the only token is this host's, in a 600 file, and
# the workflow keeps only what it already has (contents:write for the tag,
# statuses:read for the result).
set -euo pipefail

REPO="${FACTORY_REPO:-epsil2/nourtech}"
APP="${FACTORY_APP_DIR:-/opt/agent-factory/app}"
STATE="${FACTORY_STATE_DIR:-/opt/agent-factory/state}"
TOKEN_FILE="${FACTORY_GH_TOKEN_FILE:-/root/.hermes/secrets/github.env}"
CONTEXT="factory/production"
LOG="$STATE/release-watcher.log"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"$LOG"; }

mkdir -p "$STATE"
if [ -f "$LOG" ] && [ "$(wc -l <"$LOG")" -gt 5000 ]; then
  tail -n 2000 "$LOG" >"$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

[ -f "$TOKEN_FILE" ] || { log "fatal: token file $TOKEN_FILE missing"; exit 1; }
TOKEN="$(sed -n 's/^GITHUB_TOKEN=//p' "$TOKEN_FILE" | head -1)"
[ -n "$TOKEN" ] || { log "fatal: empty GITHUB_TOKEN"; exit 1; }

api() {
  curl -sS --max-time 60 \
    -H "Authorization: Bearer $TOKEN" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" "$@"
}

post_status() { # sha state description
  api -X POST -H "Content-Type: application/json" \
    "https://api.github.com/repos/$REPO/statuses/$1" \
    -d "{\"state\":\"$2\",\"context\":\"$CONTEXT\",\"description\":\"$3\"}" \
    >/dev/null 2>&1 || log "warning: could not post status $2 on $1"
}

# Tags present remotely. Two accepted shapes:
#   release/<sha>    machine tag published by .github/workflows/release.yml
#   SP<n>-Release    human sprint release tag, e.g. SP2-Release
# Annotated tags are dereferenced so the commit -- never the tag object -- is what
# gets validated against main and deployed.
# Candidate tag -> commit. An annotated tag carries its own object sha, distinct
# from the commit it points at: deploying the tag object fails the ancestry check
# below. `git ls-remote` only prints the peeled `^{}` line when the glob is written
# to match that peeled name, so resolution goes through the refs API instead, in
# scripts/release_tags.py. The helper returns the newest candidate, whether or not
# it is already deployed: a re-published tag must still produce an answer for the
# release job that is waiting on the commit status.
HELPER="${FACTORY_TAGS_HELPER:-$(dirname "$0")/release_tags.py}"
[ -x "$HELPER" ] || HELPER="${FACTORY_APP_DIR:-/opt/agent-factory/app}/scripts/release_tags.py"
[ -f "$HELPER" ] || { log "fatal: release_tags.py not found"; exit 1; }
TAG_LINE="$(python3 "$HELPER" | head -1)" || exit 0
[ -n "$TAG_LINE" ] || { log "idle: no release tag to consider"; exit 0; }
read -r RELEASE_TAG ORDERED_SHA _WHEN <<<"$TAG_LINE"
printf '%s' "$ORDERED_SHA" | grep -Eq '^[0-9a-f]{40}$' || { log "fatal: bad sha from release_tags.py"; exit 1; }
log "candidate $RELEASE_TAG -> $ORDERED_SHA"

# The tag must point at a commit already on main (never deploy a side branch)
CMP="$(api "https://api.github.com/repos/$REPO/compare/main...$ORDERED_SHA" | sed -n 's/.*"status": "\([a-z]*\)".*/\1/p' | head -1)"
if [ "$CMP" != "behind" ] && [ "$CMP" != "identical" ]; then
  log "refused $ORDERED_SHA ($RELEASE_TAG): not on main (compare status=${CMP:-unknown})"
  post_status "$ORDERED_SHA" "error" "commit is not on main"
  echo "$ORDERED_SHA" >>"$STATE/failed-tags"
  exit 0
fi

# Idempotence: an already released commit answers the waiting release job with its
# verdict instead of staying silent, so publishing the same tag twice (or re-running
# the workflow) is safe and can never downgrade the running stack.
if grep -qxF "$ORDERED_SHA" "$STATE/deployed-tags" 2>/dev/null; then
  log "already released $ORDERED_SHA ($RELEASE_TAG)"
  post_status "$ORDERED_SHA" "success" "already deployed on the VPS ($RELEASE_TAG)"
  exit 0
fi

log "releasing $RELEASE_TAG ($ORDERED_SHA)"
post_status "$ORDERED_SHA" "pending" "deploying on the VPS"

stage="$(mktemp -d /opt/agent-factory/staging.XXXXXX)"
trap 'rm -rf "$stage"' EXIT

if ! api -L "https://api.github.com/repos/$REPO/tarball/$ORDERED_SHA" | tar -xz -C "$stage" --strip-components=1; then
  log "failed: could not download $ORDERED_SHA"
  post_status "$ORDERED_SHA" "failure" "could not download the release archive"
  echo "$ORDERED_SHA" >>"$STATE/failed-tags"
  exit 1
fi

if [ ! -x "$stage/scripts/deploy_prod.sh" ] || [ ! -f "$stage/deploy/prod/docker-compose.yml" ]; then
  log "failed: archive for $ORDERED_SHA is not a deployable tree"
  post_status "$ORDERED_SHA" "error" "archive is not deployable"
  echo "$ORDERED_SHA" >>"$STATE/failed-tags"
  exit 1
fi

install -d -m 750 "$APP"
rsync -a --delete \
  --exclude 'deploy/prod/.env' --exclude 'deploy/dev/.env' \
  --exclude '.git' --exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache' \
  "$stage"/ "$APP"/
chmod +x "$APP/scripts/deploy_prod.sh"

if ( cd "$APP" && FACTORY_IMAGE_TAG="$ORDERED_SHA" ./scripts/deploy_prod.sh >>"$LOG" 2>&1 ); then
  echo "$ORDERED_SHA" >>"$STATE/deployed-tags"
  log "released $ORDERED_SHA"
  post_status "$ORDERED_SHA" "success" "deployed and healthy on the VPS ($RELEASE_TAG)"
else
  log "failed: deploy_prod.sh returned non-zero for $ORDERED_SHA (rollback may have run)"
  post_status "$ORDERED_SHA" "failure" "deploy_prod.sh failed, previous release restored"
  echo "$ORDERED_SHA" >>"$STATE/failed-tags"
  exit 1
fi
