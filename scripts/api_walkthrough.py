"""Walk the AI Factory API end-to-end against a live server.

Usage: python api_walkthrough.py [http://127.0.0.1:8098]
Prints OK/!! per call and the failing body on mismatch. Safe to re-run: every
run creates its own workflows, evaluations, artifact and release.
"""
import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8098"
HUMAN = {"x-actor": "hermes"}
COMMIT = "0123456789abcdef0123456789abcdef01234567"
STORIES = [{"key": "SCRUM-101", "summary": "Flux dashboard", "status": "Done", "type": "Story"},
           {"key": "SCRUM-102", "summary": "Metriques", "status": "Done", "type": "Story"}]


def call(method, path, body=None, expect=(200, 201), show=(), headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"content-type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            status, raw = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read().decode()
    try:
        out = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        out = {"raw": raw[:200]}
    ok = status in expect if isinstance(expect, tuple) else status == expect
    line = f"{'OK ' if ok else '!! '}{status} {method} {path}"
    if show and isinstance(out, dict):
        line += "  " + json.dumps({k: out.get(k) for k in show if k in out})
    if not ok:
        line += "\n     -> " + json.dumps(out)[:400]
    print(line)
    return out


print("== liveness (503 on readyz is normal with FACTORY_STORE=memory)")
call("GET", "/healthz", expect=(200,))
call("GET", "/readyz", expect=(200, 503))

print("== discovery: request -> PRD -> architecture gate")
wf = call("POST", "/api/v1/workflows", {"definition": "discovery",
          "input": {"request": "Exporter les runs du dashboard en CSV", "source_key": "SCRUM-101"}},
          show=("id", "status", "current_step"))
did = wf["id"]
call("GET", f"/api/v1/workflows/{did}", expect=(200,))
call("GET", f"/api/v1/workflows/{did}/checkpoints", expect=(200,))
call("POST", f"/api/v1/workflows/{did}/decision", {"decision": "approved", "note": "architecture validee"},
     show=("status",), headers=HUMAN)

print("== delivery: backlog -> plan -> implement gate -> qa -> go/no-go")
items = [{"index": 1, "acceptance": "GET /api/v1/runs.csv renvoie un CSV"}]
d = call("POST", "/api/v1/workflows", {"definition": "delivery", "input": {"backlog": {"items": items}}},
         show=("id", "current_step"))
wid = d["id"]
call("POST", f"/api/v1/workflows/{wid}/decision", {"decision": "approved", "note": "on implemente"},
     headers=HUMAN)

print("== QA evidence keyed on the workflow id (gate refuses without it)")
ev = call("POST", "/api/v1/evaluations", {"subject": wid, "dataset": "quality",
          "output": "## Livraison\n1. plan\n2. preuve\nAcceptance criteria: signe\n", "threshold": 0.8},
          show=("id", "score"))
call("POST", f"/api/v1/evaluations/{ev['id']}/review", {"outcome": "accepted"}, headers=HUMAN)
call("POST", f"/api/v1/workflows/{wid}/decision", {"decision": "approved", "note": "go dev"},
     show=("status",), headers=HUMAN)

print("== release: artifact -> verify -> readiness -> plan -> approve -> notes -> publish")
call("POST", "/api/v1/artifacts", {"version": "0.6.0", "commit_sha": COMMIT,
     "tests": {"passed": 1, "failed": 0}, "stories": STORIES, "workflow": "ci",
     "source": "github", "image": f"agent-factory-api:{COMMIT[:7]}",
     "built_at": "2026-01-01T00:00:00Z"}, show=("version", "commit_sha"))
call("GET", f"/api/v1/artifacts/{COMMIT}/verify", expect=(200,), show=("verified", "problems"))
call("POST", "/api/v1/releases/readiness", {"commit_sha": COMMIT, "target": "dev",
     "stories": STORIES, "blockers": []}, show=("ready", "missing", "risk"))

print("== an accepted evaluation on the commit unlocks readiness")
# meme contenu que la preuve QA acceptee plus haut : court, il ne passe pas le seuil
evc = call("POST", "/api/v1/evaluations", {"subject": COMMIT, "dataset": "quality",
           "output": "## Livraison\n1. plan\n2. preuve\nAcceptance criteria: signe\n",
           "threshold": 0.8}, show=("id", "score"))
call("POST", f"/api/v1/evaluations/{evc['id']}/review", {"outcome": "accepted"}, headers=HUMAN)
call("POST", "/api/v1/releases/readiness", {"commit_sha": COMMIT, "target": "dev",
     "stories": STORIES, "blockers": []}, show=("ready", "missing", "risk"))
rel = call("POST", "/api/v1/releases/plan", {"commit_sha": COMMIT, "target": "dev",
           "stories": STORIES}, show=("id", "status", "target"))
rid = rel.get("id", "")
if rid:
    call("POST", f"/api/v1/releases/{rid}/approve", {}, headers=HUMAN, show=("status",))
    call("POST", f"/api/v1/releases/{rid}/notes", {}, headers=HUMAN)
    call("POST", f"/api/v1/releases/{rid}/publish", {}, headers=HUMAN, show=("status",))
else:
    print("!! no release planned (readiness refused)")

print("== observability")
call("GET", "/api/v1/runs?limit=5", expect=(200,))
call("GET", "/api/v1/observability/audit?limit=5", expect=(200,))
call("GET", "/api/v1/observability/resources", expect=(200,))
call("GET", "/api/v1/metrics", expect=(200,))
call("GET", "/api/v1/integrations/jira/status", expect=(200, 503), show=("configured", "site"))
