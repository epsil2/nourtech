#!/usr/bin/env python3
"""Smoke test for the Camilia dashboard — stdlib only.

    python3 scripts/smoke_camilia.py http://127.0.0.1:8090

Checks what the browser actually gets: the shell, the hashed bundle, the same-origin
API proxy, and the SPA fallback. Exit code 0 only if every check passes.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8090").rstrip("/")
CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{' — ' + detail if detail else ''}")


def get(path: str, timeout: int = 10) -> tuple[int, str, dict[str, str]]:
    req = urllib.request.Request(BASE + path, headers={"accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, res.read().decode("utf-8", "replace"), dict(res.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace"), dict(exc.headers)
    except Exception as exc:  # noqa: BLE001 - network failure is a check failure
        return 0, f"{type(exc).__name__}: {exc}", {}


def main() -> int:
    status, body, headers = get("/healthz-static")
    check("nginx liveness", status == 200 and body.strip() == "ok", f"{status}")

    status, body, headers = get("/")
    check("shell served", status == 200 and "Camilia" in body, f"{status}")
    check("noindex", headers.get("X-Robots-Tag", "").startswith("noindex"), headers.get("X-Robots-Tag", "-"))
    check("nosniff", headers.get("X-Content-Type-Options") == "nosniff", headers.get("X-Content-Type-Options", "-"))

    match = re.search(r'src="(/assets/[^"]+\.js)"', body)
    check("bundle referenced", match is not None)
    if match:
        status, js, headers = get(match.group(1))
        check("bundle served", status == 200 and "Content-Type" in headers, f"{status}, {len(js)} B")

    status, body, _ = get("/api/v1/metrics")
    ok = status == 200
    store = "?"
    if ok:
        try:
            store = json.loads(body).get("store", "?")
        except json.JSONDecodeError:
            ok = False
    check("API proxy /api/v1/metrics", ok, f"{status}, store={store}")

    status, body, _ = get("/healthz")
    check("API health through proxy", status == 200, f"{status}")

    status, body, _ = get("/agents/does-not-exist")
    check("SPA fallback", status == 200 and "Camilia" in body, f"{status}")

    failed = [c for c in CHECKS if not c[1]]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
