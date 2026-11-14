#!/usr/bin/env python3
"""Resolve the newest deployable release tag to the commit it points at.

Why this exists: an annotated tag has its own object sha, distinct from the commit
it points at. Deploying the tag object fails the "is it on main?" check, and
`git ls-remote` only prints the peeled `^{}` line when the glob is written so that
it matches the peeled name too -- which is easy to get wrong silently. The refs API
is authoritative: it says whether a ref points at a tag object or at a commit, and
`/git/tags/<sha>` gives the commit of a tag object.

Prints "<tag> <40-hex commit>" for the newest candidate, or nothing.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

REPO = os.environ.get("FACTORY_REPO", "epsil2/nourtech")
TOKEN_FILE = os.environ.get("FACTORY_GH_TOKEN_FILE", "/root/.hermes/secrets/github.env")
STATE = os.environ.get("FACTORY_STATE_DIR", "/opt/agent-factory/state")
SHAPE = re.compile(r"^(release/.+|SP[0-9]+-[Rr]elease)$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")


def token() -> str:
    with open(TOKEN_FILE, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("GITHUB_TOKEN="):
                value = line.split("=", 1)[1].strip()
                if value:
                    return value
    raise SystemExit("fatal: empty GITHUB_TOKEN")


def api(path: str, tok: str):
    request = urllib.request.Request(
        "https://api.github.com" + path,
        headers={
            "Authorization": f"Bearer {tok}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "factory-release-watcher",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def already(path: str, sha: str) -> bool:
    if not os.path.exists(path):
        return False
    with open(path, encoding="utf-8") as handle:
        return sha in {line.strip() for line in handle if line.strip()}


def main() -> int:
    tok = token()
    tags: list[dict] = []
    page = 1
    while True:
        batch = api(f"/repos/{REPO}/git/refs/tags?per_page=100&page={page}", tok)
        if not batch:
            break
        tags.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    candidates: list[tuple[str, str, str]] = []
    for ref in tags:
        name = ref["ref"][len("refs/tags/") :]
        if not SHAPE.match(name):
            continue
        sha = ref["object"]["sha"]
        if ref["object"]["type"] == "tag":
            sha = api(f"/repos/{REPO}/git/tags/{sha}", tok)["object"]["sha"]
        if not HEX40.match(sha):
            continue
        # An already-deployed commit is still returned: re-publishing the same tag
        # must produce an answer for the waiting release job instead of silence.
        # Only a failed commit is skipped, so a broken release is not retried in a
        # loop by the one-minute timer.
        if already(os.path.join(STATE, "failed-tags"), sha):
            continue
        commit = api(f"/repos/{REPO}/commits/{sha}", tok)
        when = commit["commit"]["committer"]["date"]
        candidates.append((when, name, sha))

    if not candidates:
        return 0
    candidates.sort(reverse=True)
    when, name, sha = candidates[0]
    print(f"{name} {sha} \"{when}\"")
    print(f"# newest {when} among {len(candidates)} candidate(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
