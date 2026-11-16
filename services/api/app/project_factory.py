"""Project Factory: provisioning state machine, Jira and GitHub providers.

The dashboard creates a *project request*; this module turns it into real external
resources in a fixed, resumable order. Three rules shape the implementation:

* one step, one recorded operation — an operation carries its idempotency key, its
  attempt count, its timestamps and its external id, so a retry resumes instead of
  duplicating;
* nothing is ever deleted automatically. If GitHub fails after Jira succeeded, the
  Jira project stays and the failure is recorded as-is;
* every external call is preceded by a lookup. A key that already exists in Jira, or
  a repository that already exists in GitHub, is adopted rather than recreated.

Steps run in a background job (``cp_jobs``); the browser polls the provisioning state.
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from . import project_templates

# --------------------------------------------------------------------------- #
# states
# --------------------------------------------------------------------------- #
DRAFT = "DRAFT"
SPECIFICATION_READY = "SPECIFICATION_READY"
AWAITING_APPROVAL = "AWAITING_APPROVAL"
APPROVED = "APPROVED"
CREATING_JIRA = "CREATING_JIRA"
JIRA_CREATED = "JIRA_CREATED"
CREATING_GITHUB = "CREATING_GITHUB"
GITHUB_CREATED = "GITHUB_CREATED"
APPLYING_TEMPLATE = "APPLYING_TEMPLATE"
CONFIGURING_CICD = "CONFIGURING_CICD"
REGISTERING_HERMES_WORKSPACE = "REGISTERING_HERMES_WORKSPACE"
VALIDATING = "VALIDATING"
READY = "READY"
DEVELOPMENT_RUNNING = "DEVELOPMENT_RUNNING"
DEVELOPMENT_COMPLETED = "DEVELOPMENT_COMPLETED"

FAILED = "FAILED"
BLOCKED = "BLOCKED"
CANCELLED = "CANCELLED"
REQUIRES_INTERVENTION = "REQUIRES_INTERVENTION"

STATES = (
    DRAFT, SPECIFICATION_READY, AWAITING_APPROVAL, APPROVED,
    CREATING_JIRA, JIRA_CREATED, CREATING_GITHUB, GITHUB_CREATED,
    APPLYING_TEMPLATE, CONFIGURING_CICD, REGISTERING_HERMES_WORKSPACE,
    VALIDATING, READY, DEVELOPMENT_RUNNING, DEVELOPMENT_COMPLETED,
    FAILED, BLOCKED, CANCELLED, REQUIRES_INTERVENTION,
)

PROJECT_TYPES = (
    "Web Application", "Backend API", "AI Agent", "RAG Application",
    "Automation", "Microservices", "Custom",
)

VISIBILITIES = ("private", "internal", "public")

STEP_ORDER = (
    "create_jira",
    "create_repository",
    "apply_template",
    "configure_cicd",
    "register_workspace",
    "validate",
)

# state reached after each step succeeds
STEP_STATE = {
    "create_jira": JIRA_CREATED,
    "create_repository": GITHUB_CREATED,
    "apply_template": GITHUB_CREATED,
    "configure_cicd": CONFIGURING_CICD,
    "register_workspace": REGISTERING_HERMES_WORKSPACE,
    "validate": READY,
}


class ProvisioningError(RuntimeError):
    """A step failed in a way that must be surfaced to the operator verbatim."""

    def __init__(self, message: str, *, retryable: bool = True, evidence: dict | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.evidence = evidence or {}


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------- #
# documents
# --------------------------------------------------------------------------- #
PROJECT = "factory_project"
SPEC = "factory_spec"
PROVISIONING = "factory_provisioning"
OPERATION = "factory_operation"
JIRA_PROJECT = "factory_jira_project"
JIRA_ISSUE = "factory_jira_issue"
REPO = "factory_repo"
WORKSPACE = "factory_workspace"
APPROVAL = "factory_approval"
EXECUTION = "factory_execution"


def project_get(store, project_id: str) -> dict[str, Any] | None:
    try:
        return store.cp_get(PROJECT, project_id)
    except Exception:  # malformed uuid
        return None


def project_by_key(store, key: str) -> dict[str, Any] | None:
    rows = store.cp_list(PROJECT, where={"key": key.upper()}, limit=1)
    return rows[0] if rows else None


def projects(store, limit: int = 100) -> list[dict[str, Any]]:
    return store.cp_list(PROJECT, limit=limit)


def provisioning_of(store, project_id: str) -> dict[str, Any] | None:
    rows = store.cp_list(PROVISIONING, where={"project_id": project_id}, limit=1)
    return rows[0] if rows else None


def ensure_provisioning(store, project_id: str) -> dict[str, Any]:
    found = provisioning_of(store, project_id)
    if found:
        return found
    return store.cp_save(
        PROVISIONING,
        {"project_id": project_id, "state": DRAFT, "last_error": None, "updated_at": now()},
        project_id=project_id,
    )


def set_state(store, project_id: str, state: str, **extra: Any) -> dict[str, Any]:
    record = ensure_provisioning(store, project_id)
    fields = {"state": state, "updated_at": now(), **extra}
    if state in {FAILED, BLOCKED, CANCELLED, REQUIRES_INTERVENTION}:
        fields.setdefault("last_error", extra.get("last_error"))
    return store.cp_patch(PROVISIONING, record["id"], fields) or record


def state_of(store, project_id: str) -> str:
    record = provisioning_of(store, project_id)
    return (record or {}).get("state", DRAFT)


def spec_latest(store, project_id: str) -> dict[str, Any] | None:
    rows = store.cp_list(SPEC, where={"project_id": project_id}, limit=20)
    if not rows:
        return None
    return sorted(rows, key=lambda r: int(r.get("version") or 0))[-1]


def spec_save(
    store, project_id: str, content: dict[str, Any], *, status: str = "draft", actor: str = "system"
) -> dict[str, Any]:
    previous = spec_latest(store, project_id)
    version = int((previous or {}).get("version") or 0) + 1
    doc = {
        "project_id": project_id,
        "version": version,
        "content": content,
        "status": status,
        "created_by": actor,
        "created_at": now(),
    }
    return store.cp_save(SPEC, doc, project_id=project_id)


def spec_approve(store, project_id: str, actor: str) -> dict[str, Any]:
    latest = spec_latest(store, project_id)
    if not latest:
        raise ValueError("no specification to approve")
    if latest.get("status") == "approved":
        return latest
    record = store.cp_patch(
        SPEC,
        latest["id"],
        {"status": "approved", "approved_by": actor, "approved_at": now()},
    ) or latest
    store.add_audit(actor, "spec.approved", "project", project_id, {"version": latest.get("version")})
    return record


# --------------------------------------------------------------------------- #
# operations (idempotency lives here)
# --------------------------------------------------------------------------- #
def operation_key(project_id: str, step: str) -> str:
    return f"{project_id}:{step}"


def _operation_id(project_id: str, step: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"camila:operation:{operation_key(project_id, step)}"))


def operation_get(store, project_id: str, step: str) -> dict[str, Any] | None:
    return store.cp_get(OPERATION, _operation_id(project_id, step))


def operation_start(store, project_id: str, step: str) -> dict[str, Any]:
    existing = operation_get(store, project_id, step)
    if existing:
        attempt = int(existing.get("attempt") or 0) + 1
        return store.cp_patch(
            OPERATION,
            existing["id"],
            {
                "status": "running",
                "attempt": attempt,
                "started_at": now(),
                "error": None,
            },
        ) or existing
    doc = {
        "project_id": project_id,
        "step": step,
        "idempotency_key": operation_key(project_id, step),
        "status": "running",
        "attempt": 1,
        "started_at": now(),
        "completed_at": None,
        "input_ref": None,
        "output_ref": None,
        "external_id": None,
        "error": None,
    }
    return store.cp_save(OPERATION, doc, project_id=project_id, doc_id=_operation_id(project_id, step))


def operation_finish(
    store,
    project_id: str,
    step: str,
    *,
    status: str,
    output_ref: dict[str, Any] | None = None,
    external_id: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    current = operation_get(store, project_id, step)
    if not current:
        current = operation_start(store, project_id, step)
    return store.cp_patch(
        OPERATION,
        current["id"],
        {
            "status": status,
            "completed_at": now(),
            "output_ref": output_ref or current.get("output_ref"),
            "external_id": external_id or current.get("external_id"),
            "error": error,
        },
    ) or current


def operations_of(store, project_id: str) -> list[dict[str, Any]]:
    rows = store.cp_list(OPERATION, where={"project_id": project_id}, limit=50)
    order = {step: i for i, step in enumerate(STEP_ORDER)}
    return sorted(rows, key=lambda r: (order.get(r.get("step"), 99), r.get("started_at") or ""))


def next_step(store, project_id: str) -> str | None:
    """First step that has not succeeded yet."""
    for step in STEP_ORDER:
        op = operation_get(store, project_id, step)
        if not op or op.get("status") != "succeeded":
            return step
    return None


# --------------------------------------------------------------------------- #
# secrets / config
# --------------------------------------------------------------------------- #
def _env_or_file(var: str, path: str, key: str) -> str:
    value = (os.getenv(var) or "").strip()
    if value:
        return value
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def github_credential() -> tuple[str, str]:
    """Which GitHub credential the factory will use, and whether it is usable.

    A GitHub App installation token is the machine credential (one hour, scoped to the
    installed repositories, minted from the private key). The personal token stays as the
    fallback for hosts where the App is not installed yet — never as the target state.
    """
    from . import github_app

    app_ready, detail = github_app.configured()
    if app_ready:
        return "app", detail
    pat = _env_or_file("GITHUB_TOKEN", "/opt/agent-factory/secrets/github.env", "GITHUB_TOKEN")
    if pat:
        return "pat", "jeton personnel (dépôts sélectionnés) — remplacer par une GitHub App"
    return "none", detail


def github_token() -> str:
    mode, _detail = github_credential()
    if mode == "app":
        from . import github_app

        return github_app.installation_token(github_owner())
    return _env_or_file("GITHUB_TOKEN", "/opt/agent-factory/secrets/github.env", "GITHUB_TOKEN")


def jira_settings() -> tuple[str, str, str]:
    site = _env_or_file("JIRA_SITE_URL", "/opt/agent-factory/secrets/jira.env", "JIRA_SITE_URL")
    email = _env_or_file("JIRA_ACCOUNT_EMAIL", "/opt/agent-factory/secrets/jira.env", "JIRA_ACCOUNT_EMAIL")
    token = _env_or_file("JIRA_API_TOKEN", "/opt/agent-factory/secrets/jira.env", "JIRA_API_TOKEN")
    return site.rstrip("/"), email, token


# --------------------------------------------------------------------------- #
# HTTP helper
# --------------------------------------------------------------------------- #
def _http(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> tuple[int, dict[str, Any] | list | str]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read()[:600].decode("utf-8", "replace")
        try:
            payload: Any = json.loads(raw)
        except ValueError:
            payload = raw
        return exc.code, payload
    except urllib.error.URLError as exc:
        raise ProvisioningError(f"network error contacting {urllib.parse.urlsplit(url).netloc}: {exc.reason}") from exc


# --------------------------------------------------------------------------- #
# Jira
# --------------------------------------------------------------------------- #
KEY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")


def jira_headers() -> dict[str, str]:
    site, email, token = jira_settings()
    basic = base64.b64encode(f"{email}:{token}".encode()).decode()
    return {
        "Authorization": f"Basic {basic}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def jira_ready() -> tuple[bool, str]:
    site, email, token = jira_settings()
    if not site or not email or not token:
        return False, "Jira credentials are not configured on this host"
    return True, site


def jira_project_exists(key: str) -> dict[str, Any] | None:
    site, _, _ = jira_settings()
    status, payload = _http("GET", f"{site}/rest/api/3/project/{key}", headers=jira_headers())
    if status == 200 and isinstance(payload, dict):
        return payload
    return None


def jira_myself() -> dict[str, Any]:
    site, _, _ = jira_settings()
    status, payload = _http("GET", f"{site}/rest/api/3/myself", headers=jira_headers())
    if status != 200 or not isinstance(payload, dict):
        raise ProvisioningError(f"Jira identity lookup failed (HTTP {status})", evidence={"status": status})
    return payload


def jira_create_project(key: str, name: str, description: str = "", lead_account_id: str | None = None) -> dict[str, Any]:
    """Create the project, or adopt the existing one with that key (never duplicate)."""
    existing = jira_project_exists(key)
    if existing:
        return {"created": False, "key": key, "id": existing.get("id"), "name": existing.get("name"), "url": f"{jira_settings()[0]}/jira/software/projects/{key}"}

    site, _, _ = jira_settings()
    body: dict[str, Any] = {
        "key": key,
        "name": name[:80],
        "projectTypeKey": "software",
        "projectTemplateKey": "com.pyxis.greenhopper.jira:gh-simplified-agility-kanban",
        "description": (description or f"{name} — provisioned by Camilia")[:200],
        "assigneeType": "PROJECT_LEAD",
    }
    lead = lead_account_id or jira_myself().get("accountId")
    if lead:
        body["leadAccountId"] = lead
    status, payload = _http("POST", f"{site}/rest/api/3/project", headers=jira_headers(), body=body)
    if status not in (200, 201) or not isinstance(payload, dict):
        message = payload.get("errorMessages") if isinstance(payload, dict) else payload
        raise ProvisioningError(f"Jira project creation failed (HTTP {status}): {message}", evidence={"status": status})
    store_url = f"{site}/jira/software/projects/{key}"
    return {"created": True, "key": payload.get("key", key), "id": payload.get("id"), "name": name, "url": store_url}


def jira_issue_types(project_key: str) -> dict[str, dict[str, Any]]:
    site, _, _ = jira_settings()
    status, payload = _http(
        "GET",
        f"{site}/rest/api/3/issue/createmeta/{project_key}/issuetypes?maxResults=50",
        headers=jira_headers(),
    )
    if status != 200:
        return {}
    items = payload.get("issueTypes") if isinstance(payload, dict) else None
    return {item.get("name", "").lower(): item for item in (items or [])}


def jira_create_issue(
    project_key: str,
    summary: str,
    *,
    issue_type: str = "Task",
    description: str = "",
    labels: list[str] | None = None,
    epic_key: str | None = None,
) -> dict[str, Any]:
    site, _, _ = jira_settings()
    types = jira_issue_types(project_key)
    type_name = issue_type.lower()
    if type_name not in types:
        raise ProvisioningError(
            f"issue type '{issue_type}' is not available in {project_key}",
            evidence={"available": sorted(types)},
        )
    fields: dict[str, Any] = {
        "project": {"key": project_key},
        "summary": summary[:250],
        "issuetype": {"id": types[type_name]["id"]},
    }
    if description:
        fields["description"] = {
            "type": "doc",
            "version": 1,
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": description[:3000]}]}
            ],
        }
    if labels:
        fields["labels"] = labels[:5]
    if epic_key and "epic link" in types:
        fields["parent"] = {"key": epic_key}
    status, payload = _http("POST", f"{site}/rest/api/3/issue", headers=jira_headers(), body={"fields": fields})
    if status not in (200, 201) or not isinstance(payload, dict):
        raise ProvisioningError(f"Jira issue creation failed (HTTP {status})", evidence={"status": status})
    return {"key": payload.get("key"), "id": payload.get("id"), "summary": summary, "type": issue_type}


# --------------------------------------------------------------------------- #
# GitHub
# --------------------------------------------------------------------------- #
def github_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {github_token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "camila-project-factory",
        "Content-Type": "application/json",
    }


def github_owner() -> str:
    return (os.getenv("CAMILIA_GITHUB_OWNER") or "epsil2").strip()


def github_ready() -> tuple[bool, str]:
    mode, detail = github_credential()
    if mode == "none":
        return False, detail
    if mode == "pat" and not github_token():
        return False, "aucun jeton GitHub configuré sur cet hôte"
    return True, f"{github_owner()} ({mode})"


def github_repo_exists(full_name: str) -> dict[str, Any] | None:
    status, payload = _http("GET", f"https://api.github.com/repos/{full_name}", headers=github_headers())
    if status == 200 and isinstance(payload, dict):
        return payload
    return None


_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")


def repo_description(text: str, limit: int = 350) -> str:
    """Flatten a project description into something GitHub accepts.

    The project description is multi-line markdown, and GitHub rejects any control
    character in a repository description — newlines included — with
    `422 description control characters are not allowed`. Provisioning stopped on
    that alone, so the text is flattened here, once, before it is sent.
    """
    flattened = " ".join(_CONTROL_CHARS.sub(" ", text or "").split())
    return flattened[:limit].strip()


def _github_message(payload: Any) -> str:
    """GitHub states the real cause in `errors[]`; `message` alone says nothing."""
    if not isinstance(payload, dict):
        return str(payload)
    message = str(payload.get("message") or "").strip()
    details = [
        str(item.get("message") or item.get("code") or "").strip()
        for item in (payload.get("errors") or [])
        if isinstance(item, dict)
    ]
    details = list(dict.fromkeys(detail for detail in details if detail))
    if not details:
        return message
    joined = "; ".join(details)
    return f"{message} ({joined})" if message else joined


def github_create_repo(name: str, *, private: bool = True, description: str = "") -> dict[str, Any]:
    owner = github_owner()
    full_name = f"{owner}/{name}"
    existing = github_repo_exists(full_name)
    if existing:
        return {
            "created": False,
            "full_name": existing["full_name"],
            "url": existing["html_url"],
            "default_branch": existing.get("default_branch") or "main",
            "private": existing.get("private", private),
        }
    body = {
        "name": name,
        "description": repo_description(description),
        "private": private,
        "auto_init": True,
        "has_issues": True,
        "has_wiki": False,
        "has_projects": False,
    }
    status, payload = _http("POST", "https://api.github.com/user/repos", headers=github_headers(), body=body)
    if status not in (200, 201) or not isinstance(payload, dict):
        detail = _github_message(payload)
        if status == 422 and "already exists" in detail.lower():
            # The lookup above answered 404 and the creation says the name is taken: the
            # repository exists but the token is scoped to selected repositories.
            raise ProvisioningError(
                f"le dépôt {full_name} existe déjà mais le jeton GitHub ne le voit pas "
                f"(HTTP 422: {detail}). Le jeton est limité à des dépôts sélectionnés : "
                "élargir son accès à tous les dépôts (ou installer la GitHub App), puis "
                "relancer — le dépôt existant sera adopté, pas recréé.",
                retryable=False,
                evidence={"status": status, "repository": full_name},
            )
        raise ProvisioningError(
            f"GitHub repository creation failed (HTTP {status}): {detail}",
            evidence={"status": status},
        )
    created_full_name = payload["full_name"]
    # The repository exists, but a selected-repositories token cannot reach it afterwards.
    # Detect that here rather than at the first write, so the operator sees one clear cause.
    check, _probe = _http("GET", f"https://api.github.com/repos/{created_full_name}", headers=github_headers())
    if check in (403, 404):
        raise ProvisioningError(
            f"le dépôt {created_full_name} a été créé mais le jeton GitHub n'y a pas accès "
            f"(HTTP {check}). Élargir l'accès du jeton GitHub aux nouveaux dépôts, puis relancer : "
            "le dépôt existant sera adopté, pas recréé.",
            retryable=False,
            evidence={"status": check, "repository": created_full_name},
        )
    return {
        "created": True,
        "full_name": created_full_name,
        "url": payload["html_url"],
        "default_branch": payload.get("default_branch") or "main",
        "private": payload.get("private", private),
    }


def github_commit_files(full_name: str, files: dict[str, str], message: str, branch: str = "main") -> dict[str, Any]:
    """Commit a whole file tree in one commit: blobs inline, one tree, one commit."""
    headers = github_headers()
    base = f"https://api.github.com/repos/{full_name}/git"
    status, ref = _http("GET", f"{base}/ref/heads/{branch}", headers=headers)
    if status in (403, 404):
        # A fine-grained token that is scoped to *selected* repositories can create a
        # repository it then cannot write to. Say so, instead of pretending the repo is empty.
        raise ProvisioningError(
            f"GitHub refuses access to {full_name} (HTTP {status}): le jeton GitHub est limité à "
            "des dépôts sélectionnés. Élargir son accès (Contents: read/write, Administration: "
            "read/write, Workflows: read/write, Metadata: read) puis relancer l'étape.",
            retryable=False,
            evidence={"status": status, "repository": full_name},
        )
    if status != 200 or not isinstance(ref, dict):
        raise ProvisioningError(f"cannot read branch {branch} of {full_name} (HTTP {status})")
    parent_sha = ref["object"]["sha"]
    status, commit = _http("GET", f"{base}/commits/{parent_sha}", headers=headers)
    if status != 200 or not isinstance(commit, dict):
        raise ProvisioningError(f"cannot read base commit of {full_name} (HTTP {status})")
    base_tree = commit["tree"]["sha"]

    tree = [
        {"path": path, "mode": "100644", "type": "blob", "content": content}
        for path, content in sorted(files.items())
    ]
    status, new_tree = _http("POST", f"{base}/trees", headers=headers, body={"base_tree": base_tree, "tree": tree})
    if status not in (200, 201) or not isinstance(new_tree, dict):
        raise ProvisioningError(f"cannot create tree on {full_name} (HTTP {status})")
    status, new_commit = _http(
        "POST",
        f"{base}/commits",
        headers=headers,
        body={"message": message, "tree": new_tree["sha"], "parents": [parent_sha]},
    )
    if status not in (200, 201) or not isinstance(new_commit, dict):
        raise ProvisioningError(f"cannot create commit on {full_name} (HTTP {status})")
    status, _ = _http(
        "PATCH",
        f"{base}/refs/heads/{branch}",
        headers=headers,
        body={"sha": new_commit["sha"], "force": False},
    )
    if status not in (200, 201):
        raise ProvisioningError(f"cannot update {branch} on {full_name} (HTTP {status})")
    return {"commit": new_commit["sha"], "files": len(files), "branch": branch}


def github_workflow_runs(full_name: str) -> list[dict[str, Any]]:
    status, payload = _http(
        "GET", f"https://api.github.com/repos/{full_name}/actions/runs?per_page=5", headers=github_headers()
    )
    if status != 200 or not isinstance(payload, dict):
        return []
    return [
        {
            "id": run.get("id"),
            "name": run.get("name"),
            "status": run.get("status"),
            "conclusion": run.get("conclusion"),
            "url": run.get("html_url"),
            "head_sha": run.get("head_sha"),
        }
        for run in payload.get("workflow_runs", [])
    ]


# --------------------------------------------------------------------------- #
# steps
# --------------------------------------------------------------------------- #
def _repo_name(project: dict[str, Any]) -> str:
    raw = f"{project['key'].lower()}-{_slug(project['name'])}"
    return re.sub(r"[^a-z0-9-]+", "-", raw)[:80].strip("-")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40]


def step_create_jira(store, project: dict[str, Any], actor: str) -> dict[str, Any]:
    ready, detail = jira_ready()
    if not ready:
        raise ProvisioningError(detail, retryable=False)
    result = jira_create_project(
        project["key"],
        project["name"],
        project.get("business_objective") or project.get("description", ""),
    )
    record = store.cp_save(
        JIRA_PROJECT,
        {
            "project_id": project["id"],
            "jira_key": result["key"],
            "jira_id": result.get("id"),
            "url": result["url"],
            "created": bool(result.get("created")),
        },
        project_id=project["id"],
        doc_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"camila:jira:{project['id']}")),
    )
    return {"external_id": result["key"], "output": {"jira": record}}


def step_create_repository(store, project: dict[str, Any], actor: str) -> dict[str, Any]:
    ready, detail = github_ready()
    if not ready:
        raise ProvisioningError(detail, retryable=False)
    name = _repo_name(project)
    result = github_create_repo(
        name,
        private=project.get("visibility", "private") != "public",
        description=project.get("description", ""),
    )
    record = store.cp_save(
        REPO,
        {
            "project_id": project["id"],
            "full_name": result["full_name"],
            "url": result["url"],
            "default_branch": result["default_branch"],
            "private": result["private"],
            "created": bool(result.get("created")),
        },
        project_id=project["id"],
        doc_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"camila:repo:{project['id']}")),
    )
    return {"external_id": result["full_name"], "output": {"repository": record}}


def _files_for(project: dict[str, Any]) -> dict[str, str]:
    return project_templates.materialise(project["template"], project["name"])


def step_apply_template(store, project: dict[str, Any], actor: str) -> dict[str, Any]:
    repo = _repo_of(store, project["id"])
    if not repo:
        raise ProvisioningError("repository is missing; create it first")
    files = _files_for(project)
    result = github_commit_files(
        repo["full_name"],
        files,
        f"feat: scaffold {project['name']} from template {project['template']}",
    )
    store.cp_patch(REPO, repo["id"], {"template_commit": result["commit"], "template_files": result["files"]})
    return {"external_id": result["commit"], "output": result}


def step_configure_cicd(store, project: dict[str, Any], actor: str) -> dict[str, Any]:
    repo = _repo_of(store, project["id"])
    if not repo:
        raise ProvisioningError("repository is missing; create it first")
    workflow = project_templates.generate_ci(project["template"], project["name"])
    result = github_commit_files(
        repo["full_name"],
        {".github/workflows/ci.yml": workflow},
        "ci: configure pipeline from the project template",
    )
    return {"external_id": result["commit"], "output": {"ci_commit": result["commit"]}}


def step_register_workspace(store, project: dict[str, Any], actor: str) -> dict[str, Any]:
    from .hermes_kanban import KanbanAdapter

    adapter = KanbanAdapter()
    if not adapter.available:
        raise ProvisioningError("Hermes CLI is not reachable from the API host", retryable=False)
    board = f"proj-{_slug(project['key'])}"[:40]
    adapter.ensure_board(board, f"{project['key']} — {project['name']}")
    record = store.cp_save(
        WORKSPACE,
        {
            "project_id": project["id"],
            "board": board,
            "workspace_path": f"/workspaces/{project['key'].lower()}",
            "created_at": now(),
        },
        project_id=project["id"],
        doc_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"camila:workspace:{project['id']}")),
    )
    return {"external_id": board, "output": {"workspace": record}}


def step_validate(store, project: dict[str, Any], actor: str) -> dict[str, Any]:
    repo = _repo_of(store, project["id"])
    jira = _jira_of(store, project["id"])
    checks: dict[str, Any] = {
        "jira_project": bool(jira),
        "repository": bool(repo),
        "template_committed": bool((repo or {}).get("template_commit")),
        "ci_workflow": bool((repo or {}).get("template_commit")),
    }
    runs = github_workflow_runs(repo["full_name"]) if repo else []
    checks["workflow_runs"] = len(runs)
    checks["latest_run"] = runs[0] if runs else None
    missing = [name for name, ok in checks.items() if isinstance(ok, bool) and not ok]
    if missing:
        raise ProvisioningError(f"validation failed: {', '.join(missing)}", evidence=checks)
    return {"output": checks}


def _jira_of(store, project_id: str) -> dict[str, Any] | None:
    try:
        return store.cp_get(JIRA_PROJECT, str(uuid.uuid5(uuid.NAMESPACE_URL, f"camila:jira:{project_id}")))
    except Exception:
        return None


def _repo_of(store, project_id: str) -> dict[str, Any] | None:
    try:
        return store.cp_get(REPO, str(uuid.uuid5(uuid.NAMESPACE_URL, f"camila:repo:{project_id}")))
    except Exception:
        return None


def workspace_of(store, project_id: str) -> dict[str, Any] | None:
    try:
        return store.cp_get(WORKSPACE, str(uuid.uuid5(uuid.NAMESPACE_URL, f"camila:workspace:{project_id}")))
    except Exception:
        return None


STEPS: dict[str, Callable[[Any, dict, str], dict[str, Any]]] = {
    "create_jira": step_create_jira,
    "create_repository": step_create_repository,
    "apply_template": step_apply_template,
    "configure_cicd": step_configure_cicd,
    "register_workspace": step_register_workspace,
    "validate": step_validate,
}

STEP_TITLES = {
    "create_jira": "Création du projet Jira",
    "create_repository": "Création du dépôt GitHub",
    "apply_template": "Application du template",
    "configure_cicd": "Configuration CI/CD",
    "register_workspace": "Enregistrement de l'espace Hermes",
    "validate": "Validation initiale",
}


# --------------------------------------------------------------------------- #
# orchestrator
# --------------------------------------------------------------------------- #
def advance(store, project_id: str, actor: str = "system", *, max_steps: int = 6) -> dict[str, Any]:
    """Run provisioning from the current state, resuming at the first unfinished step."""
    project = project_get(store, project_id)
    if not project:
        raise ValueError("unknown project")
    state = state_of(store, project_id)
    if state in {CANCELLED, DEVELOPMENT_RUNNING, DEVELOPMENT_COMPLETED}:
        return {"state": state, "stopped": True}
    if spec_latest(store, project_id) is None:
        return {"state": state, "stopped": True, "reason": "no specification"}

    executed = 0
    while executed < max_steps:
        step = next_step(store, project_id)
        if step is None:
            set_state(store, project_id, READY, last_error=None)
            store.add_audit(actor, "provisioning.ready", "project", project_id, {})
            return {"state": READY, "steps": executed}

        set_state(store, project_id, _state_for_step(step))
        operation_start(store, project_id, step)
        try:
            result = STEPS[step](store, project, actor)
        except ProvisioningError as exc:
            operation_finish(store, project_id, step, status="failed", error=str(exc)[:500])
            target = REQUIRES_INTERVENTION if not exc.retryable else FAILED
            set_state(store, project_id, target, last_error=str(exc)[:500], failed_step=step)
            store.add_audit(actor, "provisioning.failed", "project", project_id, {"step": step, "error": str(exc)[:300]})
            return {"state": target, "failed_step": step, "error": str(exc)}
        except Exception as exc:  # unexpected provider failure
            operation_finish(store, project_id, step, status="failed", error=f"{type(exc).__name__}: {exc}"[:500])
            set_state(store, project_id, FAILED, last_error=str(exc)[:500], failed_step=step)
            store.add_audit(actor, "provisioning.failed", "project", project_id, {"step": step, "error": str(exc)[:300]})
            return {"state": FAILED, "failed_step": step, "error": str(exc)}

        operation_finish(
            store,
            project_id,
            step,
            status="succeeded",
            output_ref=result.get("output"),
            external_id=result.get("external_id"),
        )
        set_state(store, project_id, STEP_STATE.get(step, state), last_error=None)
        store.add_audit(actor, f"provisioning.{step}", "project", project_id, {"external_id": result.get("external_id")})
        executed += 1

    if next_step(store, project_id) is None:
        set_state(store, project_id, READY, last_error=None)
        store.add_audit(actor, "provisioning.ready", "project", project_id, {})
        return {"state": READY, "steps": executed}
    return {"state": state_of(store, project_id), "steps": executed}


def _state_for_step(step: str) -> str:
    return {
        "create_jira": CREATING_JIRA,
        "create_repository": CREATING_GITHUB,
        "apply_template": APPLYING_TEMPLATE,
        "configure_cicd": CONFIGURING_CICD,
        "register_workspace": REGISTERING_HERMES_WORKSPACE,
        "validate": VALIDATING,
    }[step]


def retry_step(store, project_id: str, step: str, actor: str) -> dict[str, Any]:
    """Re-run from a failed step, only if the failure is retryable."""
    if step not in STEPS:
        raise ValueError("unknown step")
    op = operation_get(store, project_id, step)
    if op and op.get("status") == "succeeded":
        return {"state": state_of(store, project_id), "skipped": True}
    set_state(store, project_id, APPROVED, last_error=None)
    return advance(store, project_id, actor)


# --------------------------------------------------------------------------- #
# backlog plan (Jira issues)
# --------------------------------------------------------------------------- #
def backlog_plan(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn an approved specification into epics/stories, deterministic and ordered."""
    content = spec.get("content") or {}
    stories = content.get("user_stories") or content.get("stories") or []
    epics: list[dict[str, Any]] = []

    def add(epic_title: str, items: list[dict[str, Any]]) -> None:
        epics.append({"summary": epic_title, "stories": items})

    foundation = [
        {"summary": "Project skeleton, CI and local run", "description": "Bootstrap the repository, wire the generated CI and document how to run it."},
        {"summary": "Health endpoint and configuration", "description": "Expose /health and read configuration from the environment."},
    ]
    add("Fondations techniques", foundation)
    if stories:
        add(
            "Périmètre fonctionnel",
            [
                {
                    "summary": str(item.get("title") or item.get("summary") or "Story"),
                    "description": str(item.get("description") or item.get("acceptance") or ""),
                }
                for item in stories[:8]
            ],
        )
    else:
        add(
            "Périmètre fonctionnel",
            [{"summary": "Premier parcours utilisateur", "description": "Implement the first end-to-end user journey described in the specification."}],
        )
    add(
        "Qualité et livraison",
        [
            {"summary": "Tests unitaires du socle", "description": "Cover the health endpoint and the first service with real assertions.", "labels": ["Test"]},
            {"summary": "Validation de la configuration de déploiement", "description": "Validate docker compose config in CI before any release."},
        ],
    )
    return epics


def provision_backlog(store, project_id: str, actor: str = "system") -> dict[str, Any]:
    """Create the Jira backlog once, reusing issues already recorded for this project."""
    project = project_get(store, project_id)
    jira = _jira_of(store, project_id)
    if not project or not jira:
        raise ProvisioningError("Jira project is not provisioned for this project")
    spec = spec_latest(store, project_id)
    if not spec:
        raise ProvisioningError("no specification to derive the backlog from")

    existing = {row["key"]: row for row in store.cp_list(JIRA_ISSUE, where={"project_id": project_id}, limit=100)}
    created: list[dict[str, Any]] = []
    for epic in backlog_plan(spec):
        epic_doc = _create_issue_once(store, project_id, jira["jira_key"], "Epic", epic["summary"], "", [], existing)
        created.append(epic_doc)
        for story in epic["stories"]:
            story_doc = _create_issue_once(
                store, project_id, jira["jira_key"], "Story", story["summary"],
                story.get("description", ""), story.get("labels", []), existing, epic_key=epic_doc["key"],
            )
            created.append(story_doc)
    store.add_audit(actor, "jira.backlog_created", "project", project_id, {"issues": len(created)})
    return {"issues": created, "count": len(created)}


def _create_issue_once(
    store,
    project_id: str,
    jira_key: str,
    issue_type: str,
    summary: str,
    description: str,
    labels: list[str],
    existing: dict[str, dict[str, Any]],
    *,
    epic_key: str | None = None,
) -> dict[str, Any]:
    fingerprint = f"{jira_key}:{issue_type}:{summary}"
    doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"camila:issue:{project_id}:{fingerprint}"))
    known = store.cp_get(JIRA_ISSUE, doc_id)
    if known or summary in {row.get("summary") for row in existing.values()}:
        return known or next(row for row in existing.values() if row.get("summary") == summary)
    issue = jira_create_issue(
        jira_key, summary, issue_type=issue_type, description=description, labels=labels, epic_key=epic_key
    )
    record = store.cp_save(
        JIRA_ISSUE,
        {
            "project_id": project_id,
            "key": issue["key"],
            "jira_id": issue.get("id"),
            "issue_type": issue_type,
            "summary": summary,
            "epic_key": epic_key,
            "url": f"{jira_settings()[0]}/browse/{issue['key']}",
        },
        project_id=project_id,
        doc_id=doc_id,
    )
    existing[record["key"]] = record
    return record


# --------------------------------------------------------------------------- #
# bounded Hermes development task
# --------------------------------------------------------------------------- #
DEV_PROMPT = """Project: {name} ({key})
Repository: {repo}
Jira story: {issue}
Approved specification version: {spec_version}

Read the repository and its specification, then:
1. create a feature branch named feat/{slug}-{issue_lower};
2. implement the story's acceptance criteria;
3. add or update tests;
4. open a pull request against the default branch.

Constraints:
- do not deploy; production release is out of scope for this task;
- do not touch any other repository;
- stop after this single story and report what changed, with the commands you ran.
"""


def launch_development(
    store,
    project_id: str,
    *,
    issue_key: str,
    actor: str,
    runtime_limit: str = "45m",
    retry_limit: int = 1,
) -> dict[str, Any]:
    from .hermes_kanban import KanbanAdapter, HermesError, HermesUnavailable

    project = project_get(store, project_id)
    repo = _repo_of(store, project_id)
    workspace = workspace_of(store, project_id)
    spec = spec_latest(store, project_id)
    if not project or not repo or not workspace or not spec:
        raise ValueError("project is not provisioned yet")
    if state_of(store, project_id) != READY:
        raise ValueError("project is not READY")

    adapter = KanbanAdapter()
    if not adapter.available:
        raise ProvisioningError("Hermes CLI is not reachable from the API host", retryable=False)

    idempotency = f"camila:{project_id}:{issue_key}:dev1"
    body = DEV_PROMPT.format(
        name=project["name"],
        key=project["key"],
        repo=repo["full_name"],
        issue=issue_key,
        spec_version=spec.get("version"),
        slug=_slug(project["key"]),
        issue_lower=issue_key.lower().replace("-", ""),
    )
    try:
        task = adapter.create_task(
            board=workspace["board"],
            title=f"{issue_key}: implement the approved story",
            body=body,
            idempotency_key=idempotency,
            max_runtime=runtime_limit,
            max_retries=retry_limit,
            workspace="worktree",
        )
    except HermesUnavailable as exc:
        raise ProvisioningError(str(exc), retryable=False) from exc
    except HermesError as exc:
        raise ProvisioningError(f"Hermes refused the task: {exc}") from exc

    record = store.cp_save(
        EXECUTION,
        {
            "project_id": project_id,
            "task_id": task.get("id") or task.get("task_id"),
            "board": workspace["board"],
            "issue_key": issue_key,
            "status": "queued",
            "idempotency_key": idempotency,
            "runtime_limit": runtime_limit,
            "retry_limit": retry_limit,
            "launched_at": now(),
        },
        project_id=project_id,
    )
    set_state(store, project_id, DEVELOPMENT_RUNNING)
    store.add_audit(actor, "hermes.development_launched", "project", project_id, {"task": record.get("task_id")})
    return record


def executions_of(store, project_id: str) -> list[dict[str, Any]]:
    return store.cp_list(EXECUTION, where={"project_id": project_id}, limit=50)
