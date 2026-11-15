"""Project Factory API: wizard, specification, approval, provisioning, traceability.

Every route here is behind the session guard, and every mutating route additionally
requires the ``admin`` or ``operator`` role. The actor written to the audit trail is
the verified session identity — never a header, never a field sent by the browser.
"""
from __future__ import annotations

import re
import secrets
import urllib.parse
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import cp_jobs, project_factory as factory, project_templates
from .redaction import redact
from .security import actor_of, require_roles, require_user

router = APIRouter(prefix="/api/v1/factory", tags=["factory"])

OPERATOR = require_roles("admin", "operator")

KEY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")


def _store(request: Request):
    return request.app.state.store


def _project_or_404(store, project_id: str) -> dict[str, Any]:
    project = factory.project_get(store, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    return project


# --------------------------------------------------------------------------- #
# models
# --------------------------------------------------------------------------- #
class ProjectIn(BaseModel):
    key: str = Field(min_length=2, max_length=10)
    name: str = Field(min_length=2, max_length=80)
    description: str = Field(default="", max_length=4000)
    business_objective: str = Field(default="", max_length=2000)
    project_type: str = "Web Application"
    business_domain: str = Field(default="", max_length=120)
    owner: str = Field(default="", max_length=120)
    visibility: str = "private"
    template: str = "react-fastapi"
    jira_mode: str = "new"  # "new" | "existing"
    jira_existing_key: str = ""
    requirements: str = Field(default="", max_length=20000)


class ProjectPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    business_objective: str | None = None
    project_type: str | None = None
    business_domain: str | None = None
    owner: str | None = None
    visibility: str | None = None
    template: str | None = None
    requirements: str | None = None


class SpecIn(BaseModel):
    content: dict[str, Any] | None = None
    requirements: str | None = None


class ApproveIn(BaseModel):
    notes: str = Field(default="", max_length=1000)


class DevelopmentIn(BaseModel):
    issue_key: str = Field(min_length=2, max_length=40)
    runtime_limit: str = Field(default="45m", max_length=10)
    retry_limit: int = Field(default=1, ge=0, le=3)


# --------------------------------------------------------------------------- #
# reference data
# --------------------------------------------------------------------------- #
@router.get("/catalogue")
def catalogue(request: Request, _: dict = Depends(require_user)) -> dict[str, Any]:
    jira_ok, jira_detail = factory.jira_ready()
    gh_ok, gh_owner = factory.github_ready()
    return {
        "project_types": list(factory.PROJECT_TYPES),
        "visibilities": list(factory.VISIBILITIES),
        "templates": [
            {
                "key": item.key,
                "version": item.version,
                "name": item.name,
                "description": item.description,
                "stack": item.stack,
            }
            for item in project_templates.TEMPLATE_CATALOGUE
        ],
        "jira": {"ready": jira_ok, "site": jira_detail if jira_ok else None, "detail": jira_detail},
        "github": {"ready": gh_ok, "owner": gh_owner if gh_ok else None, "detail": gh_owner},
        "states": list(factory.STATES),
        "steps": [
            {"key": key, "label": factory.STEP_TITLES[key]} for key in factory.STEP_ORDER
        ],
    }


# --------------------------------------------------------------------------- #
# projects
# --------------------------------------------------------------------------- #
@router.get("/projects")
def list_projects(request: Request, _: dict = Depends(require_user)) -> dict[str, Any]:
    store = _store(request)
    rows = factory.projects(store, limit=100)
    items = []
    for project in rows:
        provisioning = factory.provisioning_of(store, project["id"]) or {}
        items.append(
            {
                "id": project["id"],
                "key": project.get("key"),
                "name": project.get("name"),
                "project_type": project.get("project_type"),
                "template": project.get("template"),
                "state": provisioning.get("state", factory.DRAFT),
                "updated_at": project.get("updated_at"),
                "created_by": project.get("created_by"),
            }
        )
    counts: dict[str, int] = {}
    for item in items:
        counts[item["state"]] = counts.get(item["state"], 0) + 1
    return {"items": items, "counts": counts, "total": len(items)}


@router.post("/projects", status_code=201)
def create_project(
    body: ProjectIn, request: Request, user: dict = Depends(OPERATOR)
) -> dict[str, Any]:
    store = _store(request)
    key = body.key.strip().upper()
    if not KEY_RE.match(key):
        raise HTTPException(status_code=422, detail="key must be 2-10 characters, letters and digits, starting with a letter")
    if factory.project_by_key(store, key):
        raise HTTPException(status_code=409, detail=f"a project with key {key} already exists")
    if body.template not in {t.key for t in project_templates.TEMPLATE_CATALOGUE}:
        raise HTTPException(status_code=422, detail="unknown template")
    if body.project_type not in factory.PROJECT_TYPES:
        raise HTTPException(status_code=422, detail="unknown project type")
    if body.visibility not in factory.VISIBILITIES:
        raise HTTPException(status_code=422, detail="unknown visibility")

    project = store.cp_save(
        factory.PROJECT,
        {
            "key": key,
            "name": body.name.strip(),
            "description": body.description.strip(),
            "business_objective": body.business_objective.strip(),
            "project_type": body.project_type,
            "business_domain": body.business_domain.strip(),
            "owner": body.owner.strip() or user["username"],
            "visibility": body.visibility,
            "template": body.template,
            "requirements": body.requirements,
            "jira_mode": body.jira_mode if body.jira_mode in {"new", "existing"} else "new",
            "jira_existing_key": body.jira_existing_key.strip().upper(),
            "created_by": user["username"],
            "status": "draft",
        },
    )
    factory.ensure_provisioning(store, project["id"])
    store.add_audit(user["username"], "factory.project_created", "project", project["id"], {"key": key})
    return {"project": detail(store, project["id"])}


@router.get("/projects/{project_id}")
def get_project(project_id: str, request: Request, _: dict = Depends(require_user)) -> dict[str, Any]:
    store = _store(request)
    _project_or_404(store, project_id)
    return detail(store, project_id)


@router.patch("/projects/{project_id}")
def patch_project(
    project_id: str, body: ProjectPatch, request: Request, user: dict = Depends(OPERATOR)
) -> dict[str, Any]:
    store = _store(request)
    project = _project_or_404(store, project_id)
    if factory.state_of(store, project_id) not in {factory.DRAFT, factory.SPECIFICATION_READY, factory.AWAITING_APPROVAL}:
        raise HTTPException(status_code=409, detail="project is locked once provisioning has started")

    fields = {k: v for k, v in body.model_dump(exclude_none=True).items() if v is not None}
    if "template" in fields and fields["template"] not in {t.key for t in project_templates.TEMPLATE_CATALOGUE}:
        raise HTTPException(status_code=422, detail="unknown template")
    if not fields:
        return {"project": detail(store, project_id)}
    store.cp_patch(factory.PROJECT, project["id"], fields)
    store.add_audit(user["username"], "factory.project_updated", "project", project_id, {"fields": sorted(fields)})
    return {"project": detail(store, project_id)}


# --------------------------------------------------------------------------- #
# specification
# --------------------------------------------------------------------------- #
@router.post("/projects/{project_id}/spec")
def write_spec(
    project_id: str, body: SpecIn, request: Request, user: dict = Depends(OPERATOR)
) -> dict[str, Any]:
    store = _store(request)
    project = _project_or_404(store, project_id)
    if body.requirements is not None:
        store.cp_patch(factory.PROJECT, project["id"], {"requirements": body.requirements})
        project["requirements"] = body.requirements
    content = body.content or generate_specification(project)
    spec = factory.spec_save(store, project_id, content, status="draft", actor=user["username"])
    if factory.state_of(store, project_id) in {factory.DRAFT, factory.SPECIFICATION_READY}:
        factory.set_state(store, project_id, factory.SPECIFICATION_READY)
    store.add_audit(user["username"], "spec.drafted", "project", project_id, {"version": spec["version"]})
    return {"spec": spec, "project": detail(store, project_id)}


@router.post("/projects/{project_id}/spec/submit")
def submit_spec(project_id: str, request: Request, user: dict = Depends(OPERATOR)) -> dict[str, Any]:
    store = _store(request)
    _project_or_404(store, project_id)
    if not factory.spec_latest(store, project_id):
        raise HTTPException(status_code=409, detail="generate the specification first")
    factory.set_state(store, project_id, factory.AWAITING_APPROVAL)
    store.add_audit(user["username"], "spec.submitted", "project", project_id, {})
    return {"project": detail(store, project_id)}


@router.post("/projects/{project_id}/approve")
def approve(
    project_id: str, body: ApproveIn, request: Request, user: dict = Depends(OPERATOR)
) -> dict[str, Any]:
    store = _store(request)
    _project_or_404(store, project_id)
    state = factory.state_of(store, project_id)
    if state not in {factory.SPECIFICATION_READY, factory.AWAITING_APPROVAL}:
        raise HTTPException(status_code=409, detail=f"cannot approve from state {state}")
    spec = factory.spec_approve(store, project_id, user["username"])
    approval = store.cp_save(
        factory.APPROVAL,
        {
            "project_id": project_id,
            "kind": "provisioning",
            "actor": user["username"],
            "decision": "approved",
            "notes": body.notes,
            "spec_version": spec.get("version"),
            "created_at": factory.now(),
        },
        project_id=project_id,
    )
    factory.set_state(store, project_id, factory.APPROVED)
    store.add_audit(user["username"], "spec.approved", "project", project_id, {"approval": approval["id"]})
    return {"project": detail(store, project_id), "approval": approval}


# --------------------------------------------------------------------------- #
# provisioning
# --------------------------------------------------------------------------- #
@router.post("/projects/{project_id}/provision", status_code=202)
def provision(project_id: str, request: Request, user: dict = Depends(OPERATOR)) -> dict[str, Any]:
    store = _store(request)
    project = _project_or_404(store, project_id)
    approved = [a for a in store.cp_list(factory.APPROVAL, where={"project_id": project_id}, limit=20) if a.get("decision") == "approved"]
    if not approved:
        raise HTTPException(status_code=409, detail="human approval is required before provisioning")
    state = factory.state_of(store, project_id)
    if state not in {factory.APPROVED, factory.FAILED, factory.REQUIRES_INTERVENTION, factory.JIRA_CREATED,
                     factory.GITHUB_CREATED, factory.CONFIGURING_CICD, factory.REGISTERING_HERMES_WORKSPACE}:
        raise HTTPException(status_code=409, detail=f"provisioning cannot start from state {state}")

    actor = user["username"]
    job = cp_jobs.submit(
        store,
        kind="factory.provision",
        subject_type="project",
        subject_id=project_id,
        project_id=project_id,
        actor=actor,
        fn=lambda: factory.advance(store, project_id, actor),
    )
    store.add_audit(actor, "provisioning.started", "project", project_id, {"job": job.get("id")})
    return {"job": job, "project": detail(store, project_id)}


@router.post("/projects/{project_id}/retry")
def retry(project_id: str, request: Request, user: dict = Depends(OPERATOR)) -> dict[str, Any]:
    store = _store(request)
    _project_or_404(store, project_id)
    state = factory.state_of(store, project_id)
    if state not in {factory.FAILED, factory.REQUIRES_INTERVENTION, factory.BLOCKED}:
        raise HTTPException(status_code=409, detail=f"nothing to retry from state {state}")
    step = factory.next_step(store, project_id)
    if not step:
        raise HTTPException(status_code=409, detail="no pending step")
    actor = user["username"]
    job = cp_jobs.submit(
        store,
        kind="factory.retry",
        subject_type="project",
        subject_id=project_id,
        project_id=project_id,
        actor=actor,
        fn=lambda: factory.advance(store, project_id, actor),
    )
    store.add_audit(actor, "provisioning.retried", "project", project_id, {"step": step, "job": job.get("id")})
    return {"job": job, "step": step}


@router.post("/projects/{project_id}/backlog", status_code=202)
def create_backlog(project_id: str, request: Request, user: dict = Depends(OPERATOR)) -> dict[str, Any]:
    store = _store(request)
    _project_or_404(store, project_id)
    if factory.state_of(store, project_id) not in {factory.JIRA_CREATED, factory.READY, factory.DEVELOPMENT_RUNNING, factory.DEVELOPMENT_COMPLETED, factory.CONFIGURING_CICD, factory.REGISTERING_HERMES_WORKSPACE}:
        raise HTTPException(status_code=409, detail="the Jira project must exist before creating the backlog")
    actor = user["username"]
    job = cp_jobs.submit(
        store,
        kind="factory.backlog",
        subject_type="project",
        subject_id=project_id,
        project_id=project_id,
        actor=actor,
        fn=lambda: factory.provision_backlog(store, project_id, actor),
    )
    return {"job": job}


@router.post("/projects/{project_id}/development", status_code=202)
def start_development(
    project_id: str, body: DevelopmentIn, request: Request, user: dict = Depends(OPERATOR)
) -> dict[str, Any]:
    store = _store(request)
    _project_or_404(store, project_id)
    try:
        record = factory.launch_development(
            store,
            project_id,
            issue_key=body.issue_key.strip().upper(),
            actor=user["username"],
            runtime_limit=body.runtime_limit,
            retry_limit=body.retry_limit,
        )
    except factory.ProvisioningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"execution": record, "project": detail(store, project_id)}


@router.get("/projects/{project_id}/executions")
def project_executions(project_id: str, request: Request, _: dict = Depends(require_user)) -> dict[str, Any]:
    store = _store(request)
    _project_or_404(store, project_id)
    return {"items": factory.executions_of(store, project_id)}


# --------------------------------------------------------------------------- #
# traceability
# --------------------------------------------------------------------------- #
@router.get("/projects/{project_id}/audit")
def project_audit(project_id: str, request: Request, _: dict = Depends(require_user)) -> dict[str, Any]:
    store = _store(request)
    _project_or_404(store, project_id)
    rows = [row for row in store.list_audit(limit=200) if str(row.get("entity_id")) == project_id]
    return {"items": rows}


def detail(store, project_id: str) -> dict[str, Any]:
    project = factory.project_get(store, project_id) or {}
    provisioning = factory.provisioning_of(store, project_id) or {}
    spec = factory.spec_latest(store, project_id)
    jira = factory._jira_of(store, project_id)
    repo = factory._repo_of(store, project_id)
    workspace = factory.workspace_of(store, project_id)
    issues = store.cp_list(factory.JIRA_ISSUE, where={"project_id": project_id}, limit=100)
    approvals = store.cp_list(factory.APPROVAL, where={"project_id": project_id}, limit=20)
    executions = factory.executions_of(store, project_id)
    operations = factory.operations_of(store, project_id)
    runs: list[dict[str, Any]] = []
    if repo and repo.get("full_name") and provisioning.get("state") in {
        factory.READY, factory.DEVELOPMENT_RUNNING, factory.DEVELOPMENT_COMPLETED
    }:
        try:
            runs = factory.github_workflow_runs(repo["full_name"])
        except Exception:  # a CI probe must never break the project page
            runs = []
    return redact(
        {
            "project": project,
            "provisioning": provisioning,
            "state": provisioning.get("state", factory.DRAFT),
            "spec": spec,
            "jira": jira,
            "jira_issues": issues,
            "repository": repo,
            "workspace": workspace,
            "approvals": approvals,
            "executions": executions,
            "operations": operations,
            "ci_runs": runs,
            "next_step": factory.next_step(store, project_id),
        }
    )


# --------------------------------------------------------------------------- #
# specification draft (deterministic, no model configured on this host)
# --------------------------------------------------------------------------- #
def generate_specification(project: dict[str, Any]) -> dict[str, Any]:
    """Draft a structured specification from the request itself.

    No language model is configured on this host, so the draft is assembled
    deterministically and labelled as such: the operator reviews and edits it before
    approving. Nothing here claims to be an AI-generated document.
    """
    name = project.get("name", "Projet")
    key = project.get("key", "PROJ")
    requirements = (project.get("requirements") or "").strip()
    objective = (project.get("business_objective") or "").strip()
    template = project.get("template", "react-fastapi")
    stack = next((t.stack for t in project_templates.TEMPLATE_CATALOGUE if t.key == template), {})
    lines = [line.strip(" -•\t") for line in requirements.splitlines() if line.strip()]
    bullets = [line for line in lines if len(line) > 3][:12] or [
        "Décrire le besoin principal de l'utilisateur",
        "Définir le périmètre du premier incrément",
    ]

    return {
        "generator": "deterministic-draft",
        "note": "Brouillon structuré produit à partir de votre saisie (aucun modèle de langage n'est configuré sur cet hôte). À relire et éditer avant approbation.",
        "business_requirements": {
            "objective": objective or f"Livrer le premier incrément utilisable de {name}.",
            "scope_in": bullets,
            "scope_out": ["Déploiement en production (approbation humaine séparée)"],
            "success_metrics": ["Le parcours principal fonctionne de bout en bout", "La CI est verte sur la branche principale"],
        },
        "prd": {
            "summary": f"{name} ({key}) — {project.get('project_type', 'Web Application')} pour {project.get('business_domain') or 'le domaine métier visé'}.",
            "users": ["Utilisateur métier", "Administrateur"],
            "problems": bullets[:5],
        },
        "functional_requirements": [
            {"id": f"FR-{i + 1:02d}", "statement": text, "priority": "must" if i < 3 else "should"}
            for i, text in enumerate(bullets)
        ],
        "non_functional_requirements": [
            {"id": "NFR-01", "statement": "Réponse < 500 ms sur les endpoints de lecture", "category": "performance"},
            {"id": "NFR-02", "statement": "Secrets uniquement côté serveur, jamais dans le dépôt", "category": "security"},
            {"id": "NFR-03", "statement": "Journalisation des actions sensibles avec identité vérifiée", "category": "audit"},
        ],
        "technical_architecture": {
            "stack": stack,
            "template": template,
            "components": ["Frontend", "API", "Base de données (si applicable)", "Pipeline CI"],
            "deployment": "VPS existant, Docker Compose, routage Traefik, environnement de test non-production",
        },
        "data_model": [
            {"entity": "Item", "fields": ["id", "title", "status", "created_at"]},
        ],
        "api_design": [
            {"method": "GET", "path": "/health", "purpose": "Sonde de vivacité"},
            {"method": "GET", "path": "/api/v1/items", "purpose": "Liste des éléments"},
            {"method": "POST", "path": "/api/v1/items", "purpose": "Création d'un élément"},
        ],
        "security_requirements": [
            "Authentification obligatoire côté serveur",
            "RBAC sur les routes mutantes",
            "Aucun secret dans le dépôt ni dans les logs",
        ],
        "acceptance_criteria": [
            f"Le projet {key} se construit et ses tests passent en CI",
            "Les endpoints /health et /api/v1/items répondent",
            "La documentation de lancement est exacte",
        ],
        "roadmap": [
            {"phase": "Sprint 1", "goal": "Socle technique, CI et health"},
            {"phase": "Sprint 2", "goal": "Premier parcours fonctionnel"},
            {"phase": "Sprint 3", "goal": "Durcissement et tests"},
        ],
        "user_stories": [
            {"title": text[:120], "description": text} for text in bullets[:6]
        ],
    }


# --------------------------------------------------------------------------- #
# GitHub App: status, OAuth client flow, installation binding
# --------------------------------------------------------------------------- #
class _StateIn(BaseModel):
    redirect_uri: str = Field(default="", max_length=400)


def _github_redirect_uri(request: Request, override: str = "") -> str:
    """Where GitHub sends the browser back. Always our own origin, never a foreign host."""
    if override:
        parsed = urllib.parse.urlparse(override)
        if parsed.scheme in {"https", "http"} and parsed.path.endswith("/github/oauth/callback"):
            return override
    base = str(request.base_url).rstrip("/")
    return f"{base}/api/v1/factory/github/oauth/callback"


@router.get("/github/status")
def github_status(request: Request, _: dict = Depends(require_user)) -> dict[str, Any]:
    """Which credential the factory would use, and what GitHub App data is still missing."""
    from . import github_app

    mode, detail = factory.github_credential()
    app_ready, app_detail = github_app.configured()
    payload: dict[str, Any] = {
        "credential": mode,
        "detail": detail,
        "owner": factory.github_owner(),
        "app_configured": app_ready,
        "app_detail": app_detail,
        "oauth_configured": bool(github_app.client_id() and github_app.client_secret()),
        "installation_id": github_app.installation_id(),
    }
    if app_ready:
        try:
            payload["installations"] = [
                {"id": str(item.get("id")), "account": (item.get("account") or {}).get("login")}
                for item in github_app.installations()
            ]
        except github_app.OAuthError as exc:
            payload["installations_error"] = exc.message
    return payload


@router.post("/github/oauth/start")
def github_oauth_start(
    body: _StateIn, request: Request, _: dict = Depends(OPERATOR)
) -> dict[str, Any]:
    """Begin the OAuth client flow: the human authorises the App, we learn the installation."""
    from . import github_app

    actor = actor_of(request)  # verified identity, not the dependency's user document
    if not (github_app.client_id() and github_app.client_secret()):
        raise HTTPException(
            status_code=409,
            detail="GitHub App OAuth incomplet : GITHUB_APP_CLIENT_ID et GITHUB_APP_CLIENT_SECRET requis",
        )
    store = _store(request)
    state = secrets.token_urlsafe(32)
    redirect_uri = _github_redirect_uri(request, body.redirect_uri)
    store.cp_save(
        "factory_oauth_state",
        {
            "state": state,
            "actor": actor,
            "redirect_uri": redirect_uri,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        doc_id=state,
    )
    store.add_audit(actor, "github.oauth_started", "github_app", "", {"redirect_uri": redirect_uri})
    return {"authorize_url": github_app.authorization_url(state=state, redirect_uri=redirect_uri), "state": state}


@router.get("/github/oauth/callback")
def github_oauth_callback(
    request: Request, code: str = "", state: str = "", error: str = "", error_description: str = ""
) -> Any:
    """GitHub's return leg: exchange the code, then bind the installation found for the App."""
    from fastapi.responses import RedirectResponse

    from . import github_app

    store = _store(request)
    actor = actor_of(request)
    if error:
        store.add_audit(actor, "github.oauth_refused", "github_app", "", {"error": error[:200]})
        return RedirectResponse(url=f"/#factory?github=refused&reason={urllib.parse.quote(error_description or error)}", status_code=303)
    if not code or not state:
        raise HTTPException(status_code=400, detail="code et state requis")
    record = store.cp_get("factory_oauth_state", state)
    if not record:
        raise HTTPException(status_code=400, detail="state inconnu ou déjà consommé")
    if record.get("actor") != actor:
        raise HTTPException(status_code=403, detail="state émis pour un autre utilisateur")
    try:
        token_payload = github_app.exchange_code(code, record.get("redirect_uri", ""))
        access = token_payload.get("access_token") or ""
        if not access:
            raise github_app.OAuthError("GitHub n'a pas renvoyé de jeton", status=400, body=token_payload)
        found = github_app.user_installations(access)
    except github_app.OAuthError as exc:
        store.add_audit(actor, "github.oauth_failed", "github_app", "", {"status": exc.status})
        return RedirectResponse(
            url=f"/#factory?github=failed&reason={urllib.parse.quote(exc.message[:200])}", status_code=303
        )
    finally:
        store.cp_delete("factory_oauth_state", state)

    owner = factory.github_owner().lower()
    matching = [
        item for item in found if ((item.get("account") or {}).get("login") or "").lower() == owner
    ] or found
    if not matching:
        store.add_audit(actor, "github.oauth_no_installation", "github_app", "", {})
        return RedirectResponse(url="/#factory?github=no-installation", status_code=303)

    installation = matching[0]
    github_app.save_setting("GITHUB_APP_INSTALLATION_ID", str(installation.get("id")))
    account = (installation.get("account") or {}).get("login", "")
    store.add_audit(
        actor,
        "github.installation_bound",
        "github_app",
        str(installation.get("id")),
        redact({"account": account, "repositories": installation.get("repository_selection", "")}),
    )
    return RedirectResponse(url=f"/#factory?github=linked&account={urllib.parse.quote(account)}", status_code=303)
