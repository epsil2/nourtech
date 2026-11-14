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
TAGMAP="$(git ls-remote --tags "https://github.com/$REPO.git" \
  'refs/tags/release/*' 'refs/tags/SP*-Release' 2>/dev/null | awk '
{
  sha = $1; ref = $2; sub(/^refs\/tags\//, "", ref);
  if (ref ~ /\^\{\}$/) { sub(/\^\{\}$/, "", ref); deref[ref] = sha }
  else { plain[ref] = sha }
  if (!(ref in seen)) { seen[ref] = 1; order[++n] = ref }
}
END { for (i = 1; i <= n; i++) { t = order[i]; s = (t in deref) ? deref[t] : plain[t]; if (s != "") print t " " s } }')"
[ -n "$TAGMAP" ] || exit 0

touch "$STATE/deployed-tags" "$STATE/failed-tags" "$STATE/pending-tags"

# Candidates: recognised tag shape, 40-hex commit, not already done
CANDIDATES=""
while read -r tag sha; do
  [ -n "${sha:-}" ] || continue
  printf '%s' "$sha" | grep -Eq '^[0-9a-f]{40}$' || continue
  printf '%s' "$tag" | grep -Eq '^(release/.+|SP[0-9]+-[Rr]elease)$' || continue
  grep -qx "$sha" "$STATE/deployed-tags" && continue
  grep -qx "$sha" "$STATE/failed-tags" && continue
  CANDIDATES="$CANDIDATES$sha|$tag\n"
done <<<"$TAGMAP"
[ -n "${CANDIDATES// /}" ] || exit 0

# Order by commit date, newest first
ordered=""
while IFS='|' read -r sha tag; do
  [ -n "${sha:-}" ] || continue
  date_utc="$(api "https://api.github.com/repos/$REPO/commits/$sha" | sed -n 's/.*"date": "\([^"]*\)".*/\1/p' | tail -1)"
  ordered="$ordered${date_utc:-0000}|$sha|$tag\n"
done <<<"$(printf '%b' "$CANDIDATES")"
BEST="$(printf '%b' "$ordered" | sort -r | head -1)"
ORDERED_SHA="$(printf '%s' "$BEST" | cut -d'|' -f2)"
RELEASE_TAG="$(printf '%s' "$BEST" | cut -d'|' -f3)"
[ -n "$ORDERED_SHA" ] || exit 0

# The tag must point at a commit already on main (never deploy a side branch)
CMP="$(api "https://api.github.com/repos/$REPO/compare/main...$ORDERED_SHA" | sed -n 's/.*"status": "\([a-z]*\)".*/\1/p' | head -1)"
if [ "$CMP" != "behind" ] && [ "$CMP" != "identical" ]; then
  log "refused $ORDERED_SHA ($RELEASE_TAG): not on main (compare status=${CMP:-unknown})"
  post_status "$ORDERED_SHA" "error" "commit is not on main"
  echo "$ORDERED_SHA" >>"$STATE/failed-tags"
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
