"""Deterministic agent specification from a wizard intake (Sprint 2).

Same intake, same specification — no model is called. The Factory has no paid
provider configured, and a specification a reviewer cannot reproduce is a
specification nobody can approve. Everything below is a pure function of the
payload: order of the input lists is preserved, no timestamps inside the body,
no randomness, no external call.

The generated document is a *proposal*. It never creates Jira issues and never
registers an agent: approval is a separate, audited, human action.
"""
from __future__ import annotations

import re
from typing import Any

SPEC_VERSION = "1.0"

# Only these skills produce backlog items. An unknown skill is rejected instead of
# being silently dropped: a wizard that swallows input produces hollow stories.
SKILL_CATALOG: dict[str, dict[str, Any]] = {
    "research": {
        "label": "Recherche et veille",
        "story": "Collecter les sources utiles et les qualifier",
        "tasks": [
            "Lister les sources (sites, flux, PDF) et leur fréquence de mise à jour",
            "Écrire le collecteur et le cache local",
            "Qualifier chaque item (pertinence, fraîcheur, source)",
        ],
        "criteria": "la collecte produit des items datés, avec source et score de pertinence",
    },
    "rag": {
        "label": "Base de connaissances (RAG)",
        "story": "Indexer la base documentaire et répondre avec citations",
        "tasks": [
            "Découper les documents en passages et calculer les embeddings",
            "Stocker les vecteurs et les métadonnées (source, date, section)",
            "Répondre uniquement à partir des passages retrouvés, en citant la source",
        ],
        "criteria": "chaque réponse cite au moins une source et refuse de répondre sans passage",
    },
    "apis": {
        "label": "Intégrations API",
        "story": "Brancher les API externes avec reprise sur erreur",
        "tasks": [
            "Déclarer les endpoints, quotas et formats de réponse",
            "Implémenter l'appel avec délai, retry et backoff",
            "Journaliser les échecs sans écrire de secret dans les logs",
        ],
        "criteria": "une API indisponible produit une erreur explicite et n'interrompt pas le reste",
    },
    "jira": {
        "label": "Jira",
        "story": "Synchroniser le backlog Jira",
        "tasks": [
            "Lire les tickets du projet cible (clé, statut, sprint)",
            "Créer ou mettre à jour les tickets avec la clé d'idempotence",
            "Écrire les transitions uniquement sur un résultat vérifié",
        ],
        "criteria": "un ticket déjà créé n'est jamais dupliqué et chaque écriture est traçable",
    },
    "github": {
        "label": "GitHub",
        "story": "Préparer les branches et les pull requests",
        "tasks": [
            "Créer une branche nommée d'après la clé Jira",
            "Pousser les commits et ouvrir la pull request",
            "Rattacher les preuves (tests, build) à la pull request",
        ],
        "criteria": "aucune écriture directe sur la branche protégée, tout passe par une PR",
    },
    "coding": {
        "label": "Implémentation",
        "story": "Implémenter les stories approuvées",
        "tasks": [
            "Lire la story et ses critères d'acceptation",
            "Écrire le code et les tests associés",
            "Ouvrir la PR sans jamais marquer Done sans test vert",
        ],
        "criteria": "le code livré passe les tests et la story reste ouverte tant qu'ils échouent",
    },
    "testing": {
        "label": "Tests",
        "story": "Couvrir les critères par des tests automatisés",
        "tasks": [
            "Traduire chaque critère Given/When/Then en cas de test",
            "Exécuter la suite en local et en intégration continue",
            "Refuser la livraison si un test échoue",
        ],
        "criteria": "aucune story n'est déclarée terminée sans suite de tests verte",
    },
    "reporting": {
        "label": "Rapports",
        "story": "Produire le rapport périodique",
        "tasks": [
            "Agréger les résultats réels de la période",
            "Rédiger le rapport avec chiffres, sources et dates",
            "Afficher « n/a » quand une donnée n'est pas sourcée",
        ],
        "criteria": "le rapport ne contient aucun chiffre non sourcé",
    },
    "automation": {
        "label": "Automatisation",
        "story": "Planifier et enchaîner les exécutions",
        "tasks": [
            "Définir le déclencheur (cron ou événement) et la fenêtre d'exécution",
            "Enchaîner les étapes avec reprise et verrou anti-double exécution",
            "Alerter en cas d'échec avec le contexte utile",
        ],
        "criteria": "une exécution échouée est signalée et rejouable sans doublon",
    },
}

# Tools map to the integration surface, never to a credential.
TOOL_CATALOG: dict[str, str] = {
    "jira": "Jira Cloud (projet SCRUM, board 1)",
    "github": "GitHub (dépôt du projet, PR et branches)",
    "api": "API AI Factory (workflows, runs, évaluations)",
    "web": "Web public en lecture",
    "postgres": "PostgreSQL (lecture, base logique dédiée)",
    "redis": "Redis (file d'attente)",
}

PERMISSION_CATALOG: dict[str, str] = {
    "read": "lecture seule",
    "write": "écriture (création et mise à jour)",
    "approve": "approbation humaine (jamais déléguée à l'agent)",
    "deploy": "déploiement (toujours soumis à un Go/No-Go humain)",
}

# Human gates. They are part of the generated workflow, not an option.
HUMAN_GATES = (
    ("spec_review", "Revue de la spécification", "Approve & Create Jira Backlog"),
    ("backlog_approval", "Approbation du backlog Jira", "Approve backlog"),
    ("prod_go_no_go", "Go/No-Go avant production", "Go / No-Go"),
)

_UNSAFE = ("secret", "token", "password", "clé", "key")


def _slug(text: str, limit: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:limit] or "agent"


def _clean(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        value = " ".join(str(item).split())
        if value and value not in seen:
            seen.append(value)
    return seen


def _kpi(name: str) -> dict[str, str]:
    """A KPI is a name plus how it is measured. No target is invented: if the
    wizard did not provide one, the target stays 'à définir' and the backlog says so."""
    label, _, target = name.partition(":")
    label = label.strip()
    return {
        "name": label,
        "target": target.strip() or "à définir",
        "measure": f"valeur mesurée à chaque exécution pour « {label} », journalisée avec sa date",
    }


def _story(skill: str, index: int, outcomes: list[str]) -> dict[str, Any]:
    entry = SKILL_CATALOG[skill]
    outcome = outcomes[min(index, len(outcomes) - 1)] if outcomes else "l'objectif de l'agent"
    criteria = [
        {
            "given": "l'agent est déployé avec sa configuration validée",
            "when": f"une exécution traitant « {entry['label'].lower()} » est déclenchée",
            "then": entry["criteria"],
        }
    ]
    criteria.append(
        {
            "given": "le résultat de l'exécution est disponible",
            "when": "l'agent veut passer à l'étape suivante qui engage le système",
            "then": "une approbation humaine est requise et l'agent s'arrête en attente",
        }
    )
    return {
        "key": f"S{index + 1}",
        "skill": skill,
        "title": f"{entry['label']} — {entry['story']}",
        "as_a": "utilisateur métier",
        "i_want": entry["story"].lower(),
        "so_that": outcome,
        "acceptance": criteria,
        "tasks": list(entry["tasks"]),
        "depends_on": [f"S{index}"] if index else [],
        "definition_of_done": [
            "tests automatisés verts",
            "relecture de la pull request",
            "preuves (sortie réelle) attachées à la PR",
        ],
    }


def _risks(tools: list[str], permissions: list[str], outcomes: list[str]) -> list[dict[str, str]]:
    risks: list[dict[str, str]] = []
    if "write" in permissions:
        risks.append(
            {
                "risk": "écriture non désirée dans un système externe",
                "impact": "élevé",
                "mitigation": "clé d'idempotence, journal d'audit, approbation humaine avant écriture",
            }
        )
    if "jira" in tools:
        risks.append(
            {
                "risk": "pollution du backlog de production",
                "impact": "moyen",
                "mitigation": "projet SCRUM uniquement, labels d'idempotence, aucune suppression de ticket",
            }
        )
    if "github" in tools:
        risks.append(
            {
                "risk": "écriture directe sur une branche protégée",
                "impact": "moyen",
                "mitigation": "branches dédiées et pull requests obligatoires",
            }
        )
    if not outcomes:
        risks.append(
            {
                "risk": "objectif non mesurable",
                "impact": "élevé",
                "mitigation": "définir au moins un résultat attendu avant l'approbation",
            }
        )
    risks.append(
        {
            "risk": "coût d'exécution non maîtrisé",
            "impact": "moyen",
            "mitigation": "quota par exécution et journal des jetons consommés",
        }
    )
    return risks


def draft(intake: dict[str, Any]) -> dict[str, Any]:
    """Turn a wizard intake into a full specification proposal."""
    name = " ".join(str(intake.get("name", "")).split())
    objective = " ".join(str(intake.get("objective", "")).split())
    problem = " ".join(str(intake.get("problem", "")).split())
    if len(objective.split()) < 3:
        raise ValueError("objective is too short to produce a specification (minimum: 3 words)")

    outcomes = _clean(list(intake.get("outcomes") or []))
    constraints = _clean(list(intake.get("constraints") or []))
    kpis = _clean(list(intake.get("kpis") or []))

    skills = _clean(list(intake.get("skills") or []))
    unknown = [s for s in skills if s not in SKILL_CATALOG]
    if unknown:
        raise ValueError(f"unknown skill(s): {', '.join(sorted(unknown))}")
    if not skills:
        raise ValueError("at least one skill is required")

    tools = _clean(list(intake.get("tools") or []))
    unknown_tools = [t for t in tools if t not in TOOL_CATALOG]
    if unknown_tools:
        raise ValueError(f"unknown tool(s): {', '.join(sorted(unknown_tools))}")

    permissions = _clean(list(intake.get("permissions") or []))
    unknown_perms = [p for p in permissions if p not in PERMISSION_CATALOG]
    if unknown_perms:
        raise ValueError(f"unknown permission(s): {', '.join(sorted(unknown_perms))}")
    if "approve" not in permissions:
        permissions = ["approve", *permissions]

    for item in (*outcomes, *constraints, *kpis, objective, problem):
        lowered = item.lower()
        if any(word in lowered for word in _UNSAFE):
            raise ValueError(
                "the intake must not contain credentials or secret material: "
                "provide the integration through the settings page instead"
            )

    slug = _slug(name or objective)
    stories = [_story(skill, i, outcomes) for i, skill in enumerate(skills)]

    workflow_steps: list[dict[str, Any]] = [{"id": "intake", "actor": "humain", "action": "Décrire l'agent", "gate": False}]
    for step_id, label, _button in HUMAN_GATES[:1]:
        workflow_steps.append({"id": step_id, "actor": "humain", "action": label, "gate": True})
    for i, story in enumerate(stories):
        workflow_steps.append(
            {"id": f"dev_{i + 1}", "actor": "agent", "action": f"Implémenter {story['key']} — {story['title']}", "gate": False}
        )
        workflow_steps.append(
            {"id": f"test_{i + 1}", "actor": "agent", "action": f"Tester {story['key']} et rattacher les preuves", "gate": False}
        )
    for step_id, label, _button in HUMAN_GATES[1:]:
        workflow_steps.append({"id": step_id, "actor": "humain", "action": label, "gate": True})
    workflow_steps.append({"id": "monitoring", "actor": "agent", "action": "Mesurer les KPI et publier le rapport", "gate": False})

    stack = ["Python 3 / FastAPI", "PostgreSQL", "Redis", "Docker Compose", "GitHub Actions"]
    components = [
        {"name": "orchestrateur", "role": "enchaîne les étapes et applique les portes humaines"},
        {"name": "collecteur", "role": "récupère les entrées (API, web, documents)"},
        {"name": "mémoire", "role": "stocke les résultats et les preuves d'exécution"},
        {"name": "rapporteur", "role": "agrège les KPI réels et produit le rapport"},
    ]

    return {
        "spec_version": SPEC_VERSION,
        "status": "draft",
        "name": name or slug,
        "slug": slug,
        "objective": objective,
        "problem": problem or "à préciser",
        "outcomes": outcomes or ["à préciser avant approbation"],
        "constraints": constraints,
        "skills": skills,
        "tools": [{"id": t, "label": TOOL_CATALOG[t]} for t in tools],
        "permissions": [{"id": p, "label": PERMISSION_CATALOG[p]} for p in permissions],
        "kpis": [_kpi(k) for k in kpis] or [{"name": "à définir", "target": "à définir", "measure": "aucun KPI fourni"}],
        "llm": {
            "provider": intake.get("llm_provider") or "déterministe (aucun modèle)",
            "model": intake.get("llm_model") or "n/a",
            "note": "aucune clé n'est demandée ni stockée dans la spécification",
        },
        "architecture": {"style": "agent orchestré par étapes", "stack": stack, "components": components},
        "workflow": {"steps": workflow_steps, "human_gates": [g[0] for g in HUMAN_GATES]},
        "backlog": {
            "epic": {
                "title": f"{name or slug} — {objective[:80]}",
                "summary": problem or objective,
            },
            "stories": stories,
        },
        "test_plan": [
            {
                "scenario": f"exécution nominale de {s['key']}",
                "given": s["acceptance"][0]["given"],
                "when": s["acceptance"][0]["when"],
                "then": s["acceptance"][0]["then"],
            }
            for s in stories
        ]
        + [
            {
                "scenario": "porte humaine",
                "given": "une étape d'approbation est atteinte",
                "when": "aucune décision humaine n'est enregistrée",
                "then": "l'enchaînement s'arrête et rien n'est écrit dans les systèmes externes",
            }
        ],
        "roadmap": [
            {"sprint": 1, "goal": "socle et première story", "deliverables": [stories[0]["key"]]},
            {
                "sprint": 2,
                "goal": "reste du backlog",
                "deliverables": [s["key"] for s in stories[1:]],
            },
            {
                "sprint": 3,
                "goal": "KPI et rapport",
                "deliverables": ["tableau de bord des exécutions", "rapport périodique"],
            },
        ],
        "risks": _risks(tools, permissions, outcomes),
        "generated_by": "deterministic-generator",
    }
