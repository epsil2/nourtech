#!/usr/bin/env python3
"""Credential scan for the repository (SCRUM-21).

Patterns are assembled from fragments so that this file never contains the very
string it looks for: a scanner that trips on its own source is a scanner nobody
trusts, and a `grep` one-liner in the workflow did exactly that.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", "node_modules"}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".woff", ".woff2", ".gz", ".zip", ".tar", ".whl"}
MAX_BYTES = 2_000_000

_PATTERNS = [
    re.compile("ATA" + "TT" + r"[A-Za-z0-9_=\-]{10,}"),          # Atlassian API token
    re.compile("github_" + "pat_" + r"[A-Za-z0-9_]{20,}"),        # GitHub fine-grained PAT
    re.compile("gh" + "[pousr]_" + r"[A-Za-z0-9]{20,}"),          # GitHub classic token
    re.compile("gl" + "pat-" + r"[A-Za-z0-9_-]{20,}"),            # GitLab PAT
    re.compile("BEGIN OPENSSH " + "PRIVATE KEY"),
    re.compile("-----BEGIN RSA " + "PRIVATE KEY"),
]


def should_skip(path: Path) -> bool:
    if any(part in SKIP_DIRS for part in path.parts):
        return True
    return path.suffix.lower() in SKIP_SUFFIXES


def scan_text(text: str) -> list[str]:
    return [pattern.pattern for pattern in _PATTERNS if pattern.search(text)]


def scan_tree(root: Path) -> list[tuple[Path, str]]:
    hits: list[tuple[Path, str]] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            path = Path(current) / name
            if should_skip(path.relative_to(root)):
                continue
            try:
                if path.stat().st_size > MAX_BYTES:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for pattern in scan_text(text):
                hits.append((path.relative_to(root), pattern))
    return hits


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else ".").resolve()
    hits = scan_tree(root)
    for path, pattern in hits:
        print(f"{path}: credential-shaped string ({pattern})")
    if hits:
        print("::error::credential-shaped string committed")
        return 1
    print("secret scan: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
