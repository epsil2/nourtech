"""Deterministic PRD generation from a business request (SCRUM-25, SCRUM-34, SCRUM-35).

No model is called: the Factory is not allowed to spend on a paid provider, and a
PRD that a reviewer cannot reproduce is a PRD nobody can audit. The generator is
therefore a pure function of its input — same request, same PRD — with the source
ticket and the design decisions carried along for traceability.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

MAX_ACCEPTANCE = 8

SECTION_LABELS = {
    "contexte": "Contexte",
    "objectif": "Objectif",
    "perimetre": "Périmètre",
    "hors_perimetre": "Hors périmètre",
    "contraintes": "Contraintes",
    "risques": "Risques",
    "critères_d_acceptation": "Critères d'acceptation",
}

# Words that signal a constraint in a one-line request.
CONSTRAINT_HINTS = ("must", "doit", "sans", "without", "never", "jamais", "only", "uniquement")
RISK_HINTS = ("jira", "production", "secret", "token", "vps", "docker", "données", "data")


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\s*(?:;|\n)\s*", text.strip())
    return [p.strip(" .") for p in parts if p.strip(" .!?")]


def _slug(text: str, limit: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:limit] or "prd"


def generate(request: str, source_key: str | None = None, context: dict[str, Any] | None = None) -> dict[str, Any]:
    request = " ".join(request.split())
    if len(request) < 15 or len(request.split()) < 3:
        raise ValueError("request is too short to produce a PRD (minimum: 3 words)")
    context = context or {}
    sentences = _sentences(request)

    objective = sentences[0] if sentences else request
    constraints = [
        s
        for s in sentences
        if any(h in s.lower() for h in CONSTRAINT_HINTS) and s != objective
    ]
    # A sentence that states a constraint belongs to "contraintes", never to the
    # scope: listing it twice would let an implementer read it as optional.
    scope = [s for s in sentences[1:] if s not in constraints]
    # Anything the request does not cover is explicitly out of scope: an empty
    # section is a decision, not an omission.
    out_of_scope = context.get("out_of_scope") or [
        "aucune modification des workflows, types de tickets ou permissions Jira",
        "aucun appel à un fournisseur payant sans accord explicite",
    ]
    risks = sorted(
        {
            f"dépendance externe détectée dans la demande : {h}"
            for h in RISK_HINTS
            if h in request.lower()
        }
    ) or ["aucun risque spécifique identifié dans la demande"]

    acceptance = [
        f"la demande « {objective[:80]} » est satisfaite et vérifiable par un test",
        "le comportement est identique sur deux exécutions successives (déterministe)",
        "aucun secret, jeton ou donnée personnelle n'apparaît dans la sortie",
        "le journal d'audit contient une entrée pour chaque action réalisée",
    ]
    if constraints:
        acceptance.append("toutes les contraintes explicites de la demande sont respectées")

    decisions = [
        {
            "decision": "aucune génération par modèle de langage à ce stade",
            "reason": "aucun fournisseur payant n'est autorisé ; le résultat doit être reproductible",
        },
        {
            "decision": "la demande d'origine est conservée telle quelle",
            "reason": "elle sert de source de vérité en cas de désaccord sur la portée",
        },
    ]

    return {
        "title": f"PRD — {objective[:70]}",
        "slug": _slug(objective),
        "source_key": source_key,
        "request": request,
        "status": "draft",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sections": {
            "contexte": request,
            "objectif": objective,
            "perimetre": scope[:MAX_ACCEPTANCE] or [objective],
            "hors_perimetre": list(out_of_scope)[:MAX_ACCEPTANCE],
            "contraintes": constraints[:MAX_ACCEPTANCE] or ["aucune contrainte explicite"],
            "risques": risks[:MAX_ACCEPTANCE],
            "critères_d_acceptation": acceptance[:MAX_ACCEPTANCE],
        },
        "decisions": decisions,
        "traceability": {
            "source": "jira" if source_key else "request",
            "source_key": source_key,
            "generator": "deterministic-v1",
        },
    }


def render_markdown(prd: dict[str, Any]) -> str:
    """Human-readable rendering, used by the evidence documents."""
    lines = [f"# {prd['title']}", ""]
    if prd.get("source_key"):
        lines.append(f"*Ticket source : {prd['source_key']}*")
    for name, items in prd["sections"].items():
        lines.append(f"## {SECTION_LABELS.get(name, name.replace('_', ' ').capitalize())}")
        if isinstance(items, str):
            lines.append("")
            lines.append(items)
        else:
            for item in items:
                lines.append(f"- {item}")
        lines.append("")
    if prd.get("decisions"):
        lines.append("## Décisions de conception")
        for d in prd["decisions"]:
            lines.append(f"- **{d['decision']}** — {d['reason']}")
    return "\n".join(lines).rstrip() + "\n"
