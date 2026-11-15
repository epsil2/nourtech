"""Tests for the versioned project template library.

The templates are pure data, so everything here inspects the rendered strings:
no Docker build, no network and no writes to a working tree.
"""

from __future__ import annotations

import dataclasses
import json
import re

import pytest
from app import project_templates as templates

EXPECTED_KEYS = ("react-fastapi", "fastapi-api", "python-agent", "react-fastapi-rag")
PROJECT_NAME = "Acme Portal"
CI_PATH = ".github/workflows/ci.yml"
CREDENTIAL_PATTERNS = (
    r"\bsk-[A-Za-z0-9]{8,}",
    r"\bghp_[A-Za-z0-9]{20,}",
    r"\bAKIA[0-9A-Z]{16}",
    r"xox[baprs]-[A-Za-z0-9-]{10,}",
    r"(?i)\bpassword\s*=\s*[^\s#]+",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
)


@pytest.fixture(scope="module")
def rendered() -> dict[str, dict[str, str]]:
    """Materialise every template once."""
    return {key: templates.materialise(key, PROJECT_NAME) for key in EXPECTED_KEYS}


def _join(files: dict[str, str]) -> str:
    return "\n".join(f"{path}\n{content}" for path, content in files.items())


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------


def test_catalogue_has_exactly_four_templates() -> None:
    assert len(templates.TEMPLATE_CATALOGUE) == 4


def test_catalogue_keys_are_exact_and_unique() -> None:
    keys = [template.key for template in templates.TEMPLATE_CATALOGUE]
    assert tuple(keys) == EXPECTED_KEYS
    assert len(set(keys)) == len(keys)


def test_every_template_is_version_1_0_0() -> None:
    assert {template.version for template in templates.TEMPLATE_CATALOGUE} == {"1.0.0"}


def test_template_versions_maps_every_catalogue_entry() -> None:
    assert set(templates.TEMPLATE_VERSIONS) == set(EXPECTED_KEYS)
    for key, versions in templates.TEMPLATE_VERSIONS.items():
        assert set(versions) == {"1.0.0"}
        assert versions["1.0.0"].key == key


def test_stack_declares_the_five_layers() -> None:
    for template in templates.TEMPLATE_CATALOGUE:
        assert set(template.stack) == {"frontend", "backend", "database", "tests", "deploy"}
        assert all(isinstance(value, str) and value.strip() for value in template.stack.values())


def test_metadata_is_json_serialisable() -> None:
    for template in templates.TEMPLATE_CATALOGUE:
        assert json.loads(json.dumps(template.metadata)) == template.metadata


def test_template_is_frozen() -> None:
    template = templates.TEMPLATE_CATALOGUE[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        template.key = "other"  # type: ignore[misc]


def test_get_template_returns_the_catalogued_instance() -> None:
    template = templates.get_template("react-fastapi")
    assert template is templates.TEMPLATE_VERSIONS["react-fastapi"]["1.0.0"]
    assert templates.get_template("react-fastapi", "1.0.0") is template


def test_get_template_rejects_an_unknown_key() -> None:
    with pytest.raises(KeyError, match="unknown template"):
        templates.get_template("django-vue")


def test_get_template_rejects_an_unknown_version() -> None:
    with pytest.raises(KeyError, match="unknown version"):
        templates.get_template("fastapi-api", "9.9.9")


# ---------------------------------------------------------------------------
# Rendered scaffold shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", EXPECTED_KEYS)
def test_template_has_the_mandatory_files(key: str, rendered: dict[str, dict[str, str]]) -> None:
    files = rendered[key]
    assert "README.md" in files
    assert ".gitignore" in files
    assert ".env.example" in files
    assert "docker-compose.yml" in files
    assert CI_PATH in files
    assert any(path.endswith("Dockerfile") for path in files)


@pytest.mark.parametrize("key", EXPECTED_KEYS)
def test_template_ships_at_least_one_real_test(
    key: str, rendered: dict[str, dict[str, str]]
) -> None:
    files = rendered[key]
    test_files = [
        path
        for path in files
        if path.endswith(".py") and (path.startswith("tests/") or "/tests/" in path)
    ]
    assert test_files, f"{key} has no python test file"
    for path in test_files:
        body = files[path]
        assert "def test_" in body
        assert "assert " in body


@pytest.mark.parametrize("key", EXPECTED_KEYS)
def test_template_documents_a_health_contract(
    key: str, rendered: dict[str, dict[str, str]]
) -> None:
    files = rendered[key]
    python_bodies = [body for path, body in files.items() if path.endswith(".py")]
    documented = any("/health" in body for body in files.values())
    implemented = any('"/health"' in body or "'/health'" in body for body in python_bodies)
    assert documented, f"{key} never mentions /health"
    assert implemented, f"{key} has no health endpoint"
    endpoint = next(
        body for body in python_bodies if '"/health"' in body or "'/health'" in body
    )
    assert '"ok"' in endpoint or "'ok'" in endpoint


@pytest.mark.parametrize("key", EXPECTED_KEYS)
def test_env_example_only_declares_empty_placeholders(
    key: str, rendered: dict[str, dict[str, str]]
) -> None:
    body = rendered[key][".env.example"]
    declarations = 0
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        assert "=" in stripped, f"{key}: {stripped!r} is not a KEY= declaration"
        variable, value = stripped.split("=", 1)
        assert variable.strip(), f"{key}: empty variable name in {stripped!r}"
        assert value.strip() == "", f"{key}: {variable} must be an empty placeholder"
        declarations += 1
    assert declarations >= 3
    assert ".env is" in body or "never commit" in body.lower()


@pytest.mark.parametrize("key", EXPECTED_KEYS)
def test_readme_covers_run_test_and_deployment(
    key: str, rendered: dict[str, dict[str, str]]
) -> None:
    body = rendered[key]["README.md"]
    assert body.startswith(f"# {PROJECT_NAME}\n")
    for heading in ("## Stack", "## Run locally", "## Test", "## Deployment", "## Configuration"):
        assert heading in body, f"{key} README misses {heading}"


@pytest.mark.parametrize("key", EXPECTED_KEYS)
def test_paths_are_relative_posix_paths(key: str, rendered: dict[str, dict[str, str]]) -> None:
    for path in rendered[key]:
        assert not path.startswith("/")
        assert "\\" not in path
        assert ".." not in path.split("/")


def test_react_fastapi_has_a_real_frontend_and_backend(
    rendered: dict[str, dict[str, str]],
) -> None:
    files = rendered["react-fastapi"]
    for path in (
        "frontend/package.json",
        "frontend/tsconfig.json",
        "frontend/index.html",
        "frontend/src/main.tsx",
        "frontend/src/App.tsx",
        "backend/requirements.txt",
        "backend/app/main.py",
        "backend/tests/test_health.py",
    ):
        assert path in files, f"missing {path}"
    package = json.loads(files["frontend/package.json"])
    assert "react" in package["dependencies"]
    assert package["scripts"]["build"].startswith("tsc")
    assert "/api/v1/items" in files["backend/app/main.py"]
    assert "/api/v1/items" in files["frontend/src/App.tsx"]
    compose = files["docker-compose.yml"]
    assert "frontend:" in compose and "backend:" in compose


def test_fastapi_api_is_a_single_service(rendered: dict[str, dict[str, str]]) -> None:
    files = rendered["fastapi-api"]
    assert "app/main.py" in files
    assert "app/items.py" in files
    assert not any(path.startswith("frontend/") for path in files)
    assert "/api/v1" in files["app/main.py"]
    assert "/items" in files["app/items.py"]
    assert files["docker-compose.yml"].count("build:") == 1


def test_python_agent_loop_is_bounded_by_the_environment(
    rendered: dict[str, dict[str, str]],
) -> None:
    files = rendered["python-agent"]
    for path in ("agent/__init__.py", "agent/main.py", "requirements.txt", "tests/test_agent.py"):
        assert path in files, f"missing {path}"
    code = files["agent/main.py"] + files["agent/config.py"]
    assert "AGENT_MAX_STEPS" in code
    assert "while state.step < config.max_steps" in files["agent/main.py"]
    assert "os.getenv" in files["agent/config.py"]
    assert "test_run_stops_at_the_step_budget" in files["tests/test_agent.py"]


def test_rag_template_has_schema_and_internal_database(
    rendered: dict[str, dict[str, str]],
) -> None:
    files = rendered["react-fastapi-rag"]
    assert "/api/v1/search" in files["backend/app/main.py"]
    assert "DATABASE_URL=" in files[".env.example"]
    schema_paths = [path for path in files if path.endswith(".sql")]
    assert schema_paths, "no SQL schema file"
    schema = files[schema_paths[0]]
    assert "CREATE TABLE" in schema and "vector(" in schema

    compose = files["docker-compose.yml"]
    assert "pgdata:" in compose
    assert "POSTGRES_PASSWORD" in compose
    assert "5432:5432" not in compose
    assert "internal: true" in compose

    db_block = compose.split("  db:", 1)[1].split("  backend:", 1)[0]
    assert "ports:" not in db_block, "the database must not publish a host port"
    assert "pgdata:/var/lib/postgresql/data" in db_block
    assert "no `ports:`" in compose


# ---------------------------------------------------------------------------
# Placeholders and materialisation
# ---------------------------------------------------------------------------


def test_materialise_replaces_both_documented_tokens(
    rendered: dict[str, dict[str, str]],
) -> None:
    for files in rendered.values():
        for path, body in files.items():
            assert templates.PROJECT_NAME_TOKEN not in body, path
            assert templates.PROJECT_SLUG_TOKEN not in body, path
            stray = [
                match.start()
                for match in re.finditer(r"\{\{", body)
                if body[max(0, match.start() - 1) : match.start()] != "$"
            ]
            assert not stray, f"{path} still contains an unrendered token"


def test_materialise_derives_the_slug_from_the_project_name() -> None:
    files = templates.materialise("fastapi-api", "Acme Portal 2")
    assert 'SERVICE_NAME", "acme-portal-2-api"' in files["app/settings.py"]
    assert "acme-portal-2-api" in files["docker-compose.yml"]
    assert files["README.md"].startswith("# Acme Portal 2\n")


def test_materialise_generates_the_ci_workflow() -> None:
    files = templates.materialise("react-fastapi-rag", PROJECT_NAME)
    assert files[CI_PATH] == templates.generate_ci("react-fastapi-rag", PROJECT_NAME)


def test_materialise_applies_extra_substitutions() -> None:
    default = templates.materialise("python-agent", PROJECT_NAME)
    substituted = templates.materialise(
        "python-agent", PROJECT_NAME, {"{{unused_token}}": "ignored"}
    )
    assert substituted == default


def test_materialise_refuses_to_override_the_builtin_tokens() -> None:
    with pytest.raises(ValueError, match="cannot override"):
        templates.materialise(
            "fastapi-api", PROJECT_NAME, {templates.PROJECT_SLUG_TOKEN: "hijacked"}
        )


@pytest.mark.parametrize("bad_name", ["", "   ", "two\nlines"])
def test_materialise_rejects_unusable_project_names(bad_name: str) -> None:
    with pytest.raises(ValueError):
        templates.materialise("fastapi-api", bad_name)


def test_materialise_rejects_an_unknown_template() -> None:
    with pytest.raises(KeyError):
        templates.materialise("flask-vue", PROJECT_NAME)


def test_files_method_matches_materialise_modulo_placeholders() -> None:
    template = templates.get_template("react-fastapi")
    files = template.files()
    assert CI_PATH in files
    assert templates.PROJECT_NAME_TOKEN in files["README.md"]


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", EXPECTED_KEYS)
def test_materialise_is_deterministic(key: str) -> None:
    assert templates.materialise(key, PROJECT_NAME) == templates.materialise(key, PROJECT_NAME)


def test_generate_ci_is_deterministic() -> None:
    assert templates.generate_ci("react-fastapi", PROJECT_NAME) == templates.generate_ci(
        "react-fastapi", PROJECT_NAME
    )


def test_files_method_is_deterministic() -> None:
    template = templates.get_template("python-agent")
    assert template.files() == template.files()


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


def test_validate_paths_accepts_the_materialised_output(
    rendered: dict[str, dict[str, str]],
) -> None:
    for files in rendered.values():
        assert templates.validate_paths(files) is None


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "../escape.py",
        "app/../../escape.py",
        "app\\main.py",
        ".env",
        ".env.local",
        ".env.production",
        "secrets.yaml",
        "config/secrets.json",
        "secrets/prod.yaml",
    ],
)
def test_validate_paths_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValueError):
        templates.validate_paths({path: "content"})


def test_validate_paths_rejects_empty_code_files() -> None:
    for path in ("app/main.py", "src/App.tsx", "db/schema.sql", "docker-compose.yml"):
        with pytest.raises(ValueError, match="empty content"):
            templates.validate_paths({path: "   \n"})


def test_validate_paths_allows_an_empty_readme_and_the_env_example() -> None:
    templates.validate_paths({"README.md": "", ".env.example": "APP_ENV=\n"})


def test_validate_paths_rejects_non_string_content() -> None:
    with pytest.raises(TypeError):
        templates.validate_paths({"app/main.py": b"bytes"})  # type: ignore[dict-item]


# ---------------------------------------------------------------------------
# CI workflow
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", EXPECTED_KEYS)
def test_generate_ci_runs_checks_tests_and_a_docker_build(key: str) -> None:
    ci = templates.generate_ci(key, PROJECT_NAME)
    assert "actions/checkout@v4" in ci
    assert "actions/setup-python@v5" in ci
    assert "ruff check" in ci
    assert "pytest" in ci
    assert "docker/setup-buildx-action" in ci
    assert "docker/build-push-action" in ci
    assert "push: false" in ci
    assert "docker compose" in ci and "config --quiet" in ci
    assert "permissions:\n  contents: read" in ci
    assert "branches: [main]" in ci
    assert ci.endswith("\n")


@pytest.mark.parametrize("key", EXPECTED_KEYS)
def test_generate_ci_references_no_secrets(key: str) -> None:
    ci = templates.generate_ci(key, PROJECT_NAME)
    assert "secrets." not in ci
    assert "${{ secrets" not in ci
    assert "OPENAI_API_KEY" not in ci


def test_generate_ci_builds_the_frontend_only_when_present() -> None:
    with_frontend = templates.generate_ci("react-fastapi", PROJECT_NAME)
    assert "actions/setup-node@v4" in with_frontend
    assert "npm run build" in with_frontend
    assert "working-directory: frontend" in with_frontend

    backend_only = templates.generate_ci("fastapi-api", PROJECT_NAME)
    assert "setup-node" not in backend_only
    assert "npm" not in backend_only
    assert "working-directory" not in backend_only


def test_generate_ci_honours_the_requested_versions() -> None:
    ci = templates.generate_ci(
        "react-fastapi", PROJECT_NAME, python_version="3.11", node_version="22"
    )
    assert 'python-version: "3.11"' in ci
    assert 'node-version: "22"' in ci


@pytest.mark.parametrize("bad_python", ["3.12; rm -rf /", "", "latest"])
def test_generate_ci_rejects_malformed_versions(bad_python: str) -> None:
    with pytest.raises(ValueError):
        templates.generate_ci("fastapi-api", PROJECT_NAME, python_version=bad_python)


def test_generate_ci_rejects_an_unknown_template() -> None:
    with pytest.raises(KeyError):
        templates.generate_ci("flask-vue", PROJECT_NAME)


# ---------------------------------------------------------------------------
# Secrets hygiene
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", EXPECTED_KEYS)
def test_no_template_ships_a_credential(key: str, rendered: dict[str, dict[str, str]]) -> None:
    haystack = _join(rendered[key])
    for pattern in CREDENTIAL_PATTERNS:
        assert re.search(pattern, haystack) is None, f"{key} matched {pattern}"


@pytest.mark.parametrize("key", EXPECTED_KEYS)
def test_no_template_ships_a_populated_dotenv(
    key: str, rendered: dict[str, dict[str, str]]
) -> None:
    assert ".env" not in rendered[key]
    body = rendered[key][".env.example"]
    for line in body.splitlines():
        if "=" in line and not line.strip().startswith("#"):
            assert line.split("=", 1)[1].strip() == ""


@pytest.mark.parametrize("key", EXPECTED_KEYS)
def test_gitignore_covers_dotenv_and_build_output(
    key: str, rendered: dict[str, dict[str, str]]
) -> None:
    body = rendered[key][".gitignore"]
    assert ".env" in body
    assert "__pycache__" in body
