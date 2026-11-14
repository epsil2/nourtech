"""Integrations: what is wired, what could be, and how to verify it.

The list is built from the environment, so a provider is only ever reported as
connected when its credentials are actually present. Nothing is written here and
no secret is returned — only booleans and the name of the variable to set.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter

from .github import GitHubClient
from .jira import JiraClient
from .jira_write import JiraWriter

router = APIRouter(prefix="/api/v1/integrations", tags=["integrations"])


def _has(*names: str) -> bool:
    return all(bool(os.getenv(name)) for name in names)


PROVIDERS: list[dict[str, Any]] = [
    {
        "id": "jira",
        "label": "Jira Cloud",
        "category": "pilotage",
        "env": ["JIRA_SITE_URL", "JIRA_ACCOUNT_EMAIL", "JIRA_API_TOKEN"],
        "what": "backlog, sprints, création d'issues après approbation du backlog",
        "read": True,
        "write": True,
    },
    {
        "id": "github",
        "label": "GitHub",
        "category": "code",
        "env": ["GITHUB_TOKEN", "GITHUB_REPO"],
        "what": "dépôt, pull requests, checks, workflows, releases — en lecture",
        "read": True,
        "write": False,
    },
    {
        "id": "anthropic",
        "label": "Anthropic Claude",
        "category": "llm",
        "env": ["ANTHROPIC_API_KEY"],
        "what": "rédaction de spécifications et revue de code par modèle",
        "read": False,
        "write": False,
    },
    {
        "id": "openai",
        "label": "OpenAI (ChatGPT)",
        "category": "llm",
        "env": ["OPENAI_API_KEY"],
        "what": "rédaction, tests, revue — alternative à Claude",
        "read": False,
        "write": False,
    },
    {
        "id": "mistral",
        "label": "Mistral AI",
        "category": "llm",
        "env": ["MISTRAL_API_KEY"],
        "what": "modèle européen, données en UE",
        "read": False,
        "write": False,
    },
    {
        "id": "ollama",
        "label": "Ollama (local)",
        "category": "llm",
        "env": ["OLLAMA_URL"],
        "what": "modèle local, aucune donnée sortante",
        "read": False,
        "write": False,
    },
    {
        "id": "langfuse",
        "label": "Langfuse",
        "category": "observabilité",
        "env": ["LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"],
        "what": "traces et coût des appels de modèles",
        "read": False,
        "write": False,
    },
    {
        "id": "otel",
        "label": "OpenTelemetry / Grafana",
        "category": "observabilité",
        "env": ["OTEL_EXPORTER_OTLP_ENDPOINT"],
        "what": "métriques et traces du pipeline",
        "read": False,
        "write": False,
    },
    {
        "id": "slack",
        "label": "Slack",
        "category": "notification",
        "env": ["SLACK_WEBHOOK_URL"],
        "what": "alerte Go/No-Go et fin de sprint",
        "read": False,
        "write": False,
    },
    {
        "id": "confluence",
        "label": "Confluence",
        "category": "documentation",
        "env": ["CONFLUENCE_SITE_URL", "CONFLUENCE_API_TOKEN"],
        "what": "publication de la documentation de sprint",
        "read": False,
        "write": False,
    },
]


SUGGESTIONS: list[dict[str, str]] = [
    {
        "label": "Claude ou GPT pour la rédaction des specs",
        "why": "aujourd'hui les spécifications sont déterministes : aucun modèle n'est appelé. "
        "Un modèle apporterait des critères d'acceptation mieux tournés, mais il faut une clé et "
        "un coût par génération — à trancher, ce n'est pas un prérequis.",
    },
    {
        "label": "Langfuse pour le suivi des agents",
        "why": "dès qu'un agent appellera un modèle, il faut voir les traces et le coût par exécution. "
        "Auto-hébergeable, mais ~300 Mo de RAM : le VPS est déjà à 3,8 Go.",
    },
    {
        "label": "Slack pour les portes Go/No-Go",
        "why": "une porte humaine qui attend sans notification finit par être franchie par oubli. "
        "Un webhook entrant suffit, aucun agent à installer.",
    },
    {
        "label": "Grafana + OTLP pour les métriques",
        "why": "utile au-delà de deux services ; en dessous, la page Monitoring actuelle suffit "
        "et évite 300 à 500 Mo de RAM supplémentaires.",
    },
    {
        "label": "pgvector pour la mémoire des agents",
        "why": "pertinent quand un agent devra retrouver ses analyses précédentes. Extension à "
        "activer dans la base existante, pas de nouveau service.",
    },
]


@router.get("")
def list_integrations() -> dict[str, Any]:
    jira = JiraClient()
    github = GitHubClient()
    writer = JiraWriter()

    items: list[dict[str, Any]] = []
    for provider in PROVIDERS:
        entry = dict(provider)
        entry["configured"] = _has(*provider["env"])
        entry["missing"] = [name for name in provider["env"] if not os.getenv(name)]
        items.append(entry)

    # Live flags where the client knows more than the env alone.
    for entry in items:
        if entry["id"] == "jira":
            entry["configured"] = jira.configured
            entry["write_enabled"] = writer.enabled
            entry["site"] = getattr(jira, "site", None) or os.getenv("JIRA_SITE_URL")
        if entry["id"] == "github":
            entry["configured"] = github.configured
            entry["repo"] = github.repo or None

    configured = [i for i in items if i["configured"]]
    return {
        "items": items,
        "count": len(items),
        "configured": len(configured),
        "suggestions": SUGGESTIONS,
        "note": "un fournisseur n'est affiché connecté que si ses variables sont réellement "
        "présentes dans l'environnement du service ; aucune clé n'est renvoyée au navigateur",
    }
