"""Redaction of sensitive values before they reach a log, an audit row or a response.

One function, used by the audit writer, so a secret can never enter the store by
a path that forgot to sanitise it.
"""
from __future__ import annotations

import re
from typing import Any

MASK = "[redacted]"

# Key names whose value is never useful in an audit trail.
SENSITIVE_KEYS = re.compile(
    r"(pass(word|wd)?|secret|token|api[_-]?key|private[_-]?key|authorization|credential|cookie)",
    re.IGNORECASE,
)

# Value shapes that are secrets whatever the key is called. Built by
# concatenation so this module never contains a literal that a secret scanner
# (including ours) would flag.
PATTERNS = [
    re.compile(p)
    for p in (
        "ATA" + "TT[A-Za-z0-9]{10,}",
        "gh" + "[pusr]_[A-Za-z0-9]{20,}",
        "github_" + "pat_[A-Za-z0-9_]{20,}",
        "gl" + "pat-[A-Za-z0-9_-]{20,}",
        "sk" + "-[A-Za-z0-9]{20,}",
    )
]
MAX_DEPTH = 8
MAX_STRING = 2000


def _clean_str(value: str) -> str:
    if len(value) > MAX_STRING:
        value = value[:MAX_STRING] + "…"
    for pattern in PATTERNS:
        value = pattern.sub(MASK, value)
    return value


def redact(value: Any, depth: int = 0) -> Any:
    """Return a deep copy with secrets masked. Never raises on odd input."""
    if depth > MAX_DEPTH:
        return MASK
    if isinstance(value, dict):
        return {
            str(k): (MASK if SENSITIVE_KEYS.search(str(k)) else redact(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [redact(v, depth + 1) for v in value]
    if isinstance(value, str):
        return _clean_str(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _clean_str(str(value))


def contains_secret(value: Any) -> bool:
    """True when redaction would actually change something."""
    return redact(value) != value
