"""Scoped development plan from an approved issue (SCRUM-27, SCRUM-37, SCRUM-38).

A plan is what turns an approved story into traceable work: the scope, the branch,
the files the change may touch, the tests that must exist, and the pull request it
must end in. It is built deterministically from the story text — no provider call —
and it cannot be linked to a PR before a human approved it.
"""
from __future__ import annotations

import re
from typing import Any

KEY = re.compile(r"^SCRUM-\d{1,4}$")
PR_URL = re.compile(r"^https://github\.com/epsil2/nourtech/pull/\d+$")

# Keyword -> the parts of the repository a change is allowed to touch. Anything a
# story does not mention is out of scope by construction.
AREAS: dict[str, list[str]] = {
    "api": ["services/api/app/api.py", "services/api/app/api_workflow.py"],
    "endpoint": ["services/api/app/api.py"],
    "workflow": ["services/api/app/workflow.py", "services/api/app/api_workflow.py"],
    "checkpoint": ["services/api/app/workflow.py", "services/api/app/store.py"],
    "jira": ["services/api/app/jira.py", "services/api/app/api_jira.py"],
    "prd": ["services/api/app/prd.py", "services/api/app/api_prd.py"],
    "audit": ["services/api/app/store.py", "services/api/app/redaction.py"],
    "postgres": ["services/api/app/store.py"],
    "deploy": ["deploy/", "scripts/deploy_prod.sh", ".github/workflows/"],
    "evaluation": ["services/api/app/evaluation.py", "services/api/app/api_eval.py"],
    "plan": ["services/api/app/planner.py", "services/api/app/api_plans.py"],
}
DEFAULT_SCOPE = ["services/api/app/", "tests/"]


def _slug(text: str, limit: int = 40) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned[:limit].strip("-") or "change"


def branch_name(issue_key: str, summary: str) -> str:
    return f"feature/{issue_key.lower()}-{_slug(summary)}"


def scope_for(text: str) -> list[str]:
    lowered = text.lower()
    paths: list[str] = []
    for keyword, targets in AREAS.items():
        if keyword in lowered:
            for target in targets:
                if target not in paths:
                    paths.append(target)
    return paths or list(DEFAULT_SCOPE)


def build(issue_key: str, summary: str, description: str = "", workflow_id: str | None = None) -> dict[str, Any]:
    if not KEY.match(issue_key):
        raise ValueError("issue_key must look like SCRUM-12")
    if not summary.strip():
        raise ValueError("the story needs a summary to scope the plan")
    text = f"{summary}\n{description}"
    steps = [
        {"index": 1, "action": "lire la story et la preuve exigée", "done_when": "scope écrit, rien hors périmètre"},
        {"index": 2, "action": "implémenter le changement dans le périmètre", "done_when": "code présent, aucun fichier hors scope modifié"},
        {"index": 3, "action": "écrire les tests avant de dire que c'est fini", "done_when": "pytest vert, aucun test ignoré"},
        {"index": 4, "action": "consigner la preuve dans docs/evidence", "done_when": "commande exécutée et sortie citée"},
        {"index": 5, "action": "ouvrir la pull request liée à la story", "done_when": "URL de PR enregistrée sur le plan"},
    ]
    return {
        "issue_key": issue_key,
        "summary": summary.strip()[:200],
        "workflow_id": workflow_id,
        "branch": branch_name(issue_key, summary),
        "scope": scope_for(text),
        "steps": steps,
        "pr_title": f"{issue_key}: {summary.strip()[:80]}",
        "tests_required": ["unit", "integration", "security", "end_to_end"],
    }


def check_pr_url(url: str) -> str:
    if not PR_URL.match(url):
        raise ValueError("pull request URL must be https://github.com/epsil2/nourtech/pull/<number>")
    return url
