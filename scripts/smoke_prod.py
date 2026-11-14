#!/usr/bin/env python3
"""Smoke test of the running AI Factory, end to end, from the outside.

Read-only by default: it probes the API and Jira and asserts status codes and
payload shape. With ``--write`` it also proves the Jira write path: one idempotent
marker issue (label ``ai-factory``, always the same title), a comment, and a
round-trip transition. Nothing is ever deleted, and re-running --write never
creates a second issue.

    python3 scripts/smoke_prod.py                     # prod, read only
    python3 scripts/smoke_prod.py --write             # prod + Jira writes
    python3 scripts/smoke_prod.py --base http://127.0.0.1:8098   # a local server

Exit code 0 only if every step passed. No dependency beyond the stdlib, so it
runs on the VPS without the venv.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

ACTOR = os.getenv("SMOKE_ACTOR", "smoke-test")
MARKER = "Smoke test AI Factory (ticket technique, geré par le smoke test)"
REQUIRED_PATHS = {
    "/healthz",
    "/readyz",
    "/api/v1/prd",
    "/api/v1/workflows",
    "/api/v1/artifacts",
    "/api/v1/releases/readiness",
    "/api/v1/integrations/jira/status",
    "/api/v1/integrations/jira/issues",
}

results: list[tuple[bool, str, str]] = []


def call(base: str, method: str, path: str, body: Any = None,
         actor: bool = False) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json"}
    if data:
        headers["Content-Type"] = "application/json"
    if actor:
        headers["x-actor"] = ACTOR
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode()
            return response.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            return exc.code, {"raw": raw[:200]}
    except (urllib.error.URLError, TimeoutError) as exc:
        return 0, {"error": str(exc)}


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((ok, name, detail))
    print(f"{'OK  ' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8081")
    parser.add_argument("--write", action="store_true", help="also exercise the Jira write path")
    parser.add_argument("--project", default=os.getenv("JIRA_WRITE_PROJECT", "SCRUM").split(",")[0])
    args = parser.parse_args()
    base = args.base.rstrip("/")
    print(f"# smoke {base} (write={args.write})\n")

    # 1-2. liveness ------------------------------------------------------
    code, _ = call(base, "GET", "/healthz")
    check("/healthz", code == 200, f"HTTP {code}")
    code, _ = call(base, "GET", "/readyz")
    check("/readyz (dépendances prêtes)", code == 200, f"HTTP {code}")
    if code != 200:
        print("\nreadyz n'est pas vert : inutile de continuer.")
        return 1

    # 3. routes served ---------------------------------------------------
    code, spec = call(base, "GET", "/openapi.json")
    paths = set(spec.get("paths", {})) if code == 200 else set()
    missing = sorted(REQUIRED_PATHS - paths)
    check("openapi.json", code == 200 and not missing,
          f"{len(paths)} routes" + (f", manquantes: {missing}" if missing else ""))

    # 4-5. Jira read -----------------------------------------------------
    code, status = call(base, "GET", "/api/v1/integrations/jira/status")
    configured = bool(status.get("configured"))
    check("jira/status", code == 200 and configured,
          f"configured={configured} write_enabled={status.get('write_enabled')} "
          f"projects={status.get('write_projects')}")

    code, listing = call(
        base, "GET",
        "/api/v1/integrations/jira/issues?" + urllib.parse.urlencode(
            {"jql": f"project = {args.project} ORDER BY created DESC", "limit": 5}
        ),
    )
    items: list[dict[str, Any]] = listing.get("items") or []
    check("jira lecture (JQL)", code == 200 and bool(items),
          f"{len(items)} issue(s), première: {items[0]['key'] if items else '-'}")

    # 6. audit trail -----------------------------------------------------
    code, audit = call(base, "GET", "/api/v1/observability/audit?limit=5")
    check("audit consultable", code == 200, f"{audit.get('count', 0)} ligne(s)")

    # 7-8. refusal paths (the release chain must stay closed without CI) --
    unknown = secrets.token_hex(20)
    body = {"commit_sha": unknown, "stories": [
        {"key": f"{args.project}-52", "summary": "smoke", "status": "Done", "type": "Story"}]}
    code, payload = call(base, "POST", "/api/v1/releases/readiness", body)
    check("readiness sur commit inconnu → 404", code == 404 and "artifact" in str(payload.get("detail")),
          f"HTTP {code} {payload.get('detail')}")
    code, payload = call(
        base, "POST", "/api/v1/releases/plan",
        {**body, "target": "dev", "version": "0.0.1"},
    )
    check("plan sur commit inconnu → 404", code == 404, f"HTTP {code} {payload.get('detail')}")

    # 9. read endpoints that used to 500 ---------------------------------
    for path in ("/api/v1/releases", "/api/v1/artifacts", "/api/v1/prd?limit=3",
                 "/api/v1/workflows?limit=3", "/api/v1/observability/resources"):
        code, payload = call(base, "GET", path)
        size = payload.get("count", len(payload.get("items", []) or [])) if isinstance(payload, dict) else 0
        check(f"GET {path}", code == 200, f"HTTP {code} ({size})")

    # 10-13. Jira write --------------------------------------------------
    if args.write:
        print("\n# écriture Jira")
        code, status = call(base, "GET", "/api/v1/integrations/jira/status")
        if not status.get("write_enabled"):
            check("écriture Jira activée", False, "write_enabled=false (JIRA_WRITE_ENABLED)")
        else:
            code, created = call(
                base, "POST", "/api/v1/integrations/jira/issues",
                {"summary": MARKER, "issue_type": "Task",
                 "description": "Créé par scripts/smoke_prod.py --write. Idempotent, ne pas dupliquer."},
                actor=True,
            )
            key = created.get("key", "")
            check("jira création idempotente", code in (200, 201) and bool(key),
                  f"HTTP {code} {key} created={created.get('created')}")
            if key:
                code, again = call(base, "POST", "/api/v1/integrations/jira/issues",
                                   {"summary": MARKER, "issue_type": "Task"}, actor=True)
                check("rejeu sans doublon", code in (200, 201) and again.get("key") == key
                      and again.get("created") is False,
                      f"HTTP {code} {again.get('key')} created={again.get('created')}")

                code, commented = call(base, "POST", f"/api/v1/integrations/jira/issues/{key}/comment",
                                       {"text": "Commentaire du smoke test : le chemin d'écriture répond."},
                                       actor=True)
                check("jira commentaire", code in (200, 201) and bool(commented.get("comment_id")),
                      f"HTTP {code} id={commented.get('comment_id')}")

                code, transitions = call(base, "GET", f"/api/v1/integrations/jira/issues/{key}/transitions")
                steps: list[dict[str, Any]] = transitions.get("items", []) or []
                names = [t.get("name") for t in steps]
                check("jira transitions lisibles", code == 200 and bool(names), ", ".join(map(str, names)))

                # le ticket technique vit en "To Do" : on le remet toujours là,
                # meme si un run precedent s'est interrompu en cours de route.
                home = "To Do" if "To Do" in names else None

                roundtrip = None
                for target in names:
                    code, moved = call(base, "POST", f"/api/v1/integrations/jira/issues/{key}/transition",
                                       {"target": target}, actor=True)
                    if code != 200:
                        roundtrip = f"{target}: HTTP {code}"
                        break
                    roundtrip = f"{len(names)} transitions parcourues"
                # remettre le ticket a sa place : le smoke test ne doit rien laisser bouge
                restore_target = home or ""
                code, moved = call(base, "POST", f"/api/v1/integrations/jira/issues/{key}/transition",
                                   {"target": restore_target}, actor=True)
                restored = code == 200
                check("jira transition aller-retour", bool(roundtrip)
                      and "HTTP" not in (roundtrip or "") and restored,
                      f"{roundtrip}, retour a {restore_target}: HTTP {code}")

                code, listing = call(
                    base, "GET", "/api/v1/integrations/jira/issues?" + urllib.parse.urlencode(
                        {"jql": f"key = {key}"}))
                rows: list[dict[str, Any]] = listing.get("items") or [{}]
                row = rows[0]
                check("relecture JQL de l'issue", code == 200 and row.get("key") == key
                      and (not home or row.get("status") == home),
                      f"{row.get('key')} statut={row.get('status')} labels={row.get('labels')}")

                code, audit = call(base, "GET", f"/api/v1/observability/audit?entity_id={key}&limit=20")
                audit_rows: list[dict[str, Any]] = audit.get("items", []) or []
                actions = [r.get("action") for r in audit_rows]
                check("audit des écritures", code == 200 and any("jira.issue" in str(a) for a in actions),
                      f"{len(actions)} événement(s) sur {key}")

    failed = [name for ok, name, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} étapes vertes")
    if failed:
        print("échecs : " + "; ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
