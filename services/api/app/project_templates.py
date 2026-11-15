"""Versioned project templates for the Camilia Project Factory.

Every template is pure data: ``files()`` returns ``path -> content`` strings and
nothing in this module touches the filesystem, the clock or the network, so
materialising the same template twice always yields identical output. A caller
(the GitHub materialiser) writes the returned mapping into a fresh repository.

Placeholders
------------
Generated files use two double-brace tokens:

* ``{{project_name}}`` - the human readable project name.
* ``{{project_slug}}`` - a lowercase, hyphenated slug derived from the name.

``materialise`` replaces both, applies optional extra ``substitutions`` and
returns the finished mapping including the generated CI workflow at
``.github/workflows/ci.yml``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from textwrap import dedent

PROJECT_NAME_TOKEN = "{{project_name}}"
PROJECT_SLUG_TOKEN = "{{project_slug}}"
CI_WORKFLOW_PATH = ".github/workflows/ci.yml"

_SUPPORTED_VERSION = "1.0.0"

_CODE_SUFFIXES = frozenset(
    {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".sql",
        ".json",
        ".yml",
        ".yaml",
        ".toml",
        ".conf",
        ".css",
        ".html",
        ".sh",
    }
)


def _text(block: str) -> str:
    """Dedent a triple-quoted block and drop its leading blank line."""
    return dedent(block).lstrip("\n")


def _slugify(value: str) -> str:
    """Return a docker/package safe slug for *value*."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "app"


# ---------------------------------------------------------------------------
# Shared file fragments
# ---------------------------------------------------------------------------


def _gitignore_python() -> str:
    return _text(
        '''
        # Python
        __pycache__/
        *.py[cod]
        .venv/
        venv/
        .pytest_cache/
        .ruff_cache/
        *.egg-info/
        build/
        dist/

        # Environment - real values never belong in the repository
        .env
        .env.local
        .env.*.local

        # Editors and operating systems
        .vscode/
        .idea/
        .DS_Store

        # Logs
        *.log
        '''
    )


def _gitignore_node() -> str:
    return _text(
        '''
        # Python
        __pycache__/
        *.py[cod]
        .venv/
        venv/
        .pytest_cache/
        .ruff_cache/
        *.egg-info/

        # Node
        node_modules/
        npm-debug.log*
        .npm/

        # Frontend build output
        frontend/dist/
        dist/
        build/

        # Environment - real values never belong in the repository
        .env
        .env.local
        .env.*.local

        # Editors and operating systems
        .vscode/
        .idea/
        .DS_Store

        # Logs
        *.log
        '''
    )


def _frontend_package_json() -> str:
    return _text(
        '''
        {
          "name": "{{project_slug}}-frontend",
          "private": true,
          "version": "0.1.0",
          "type": "module",
          "scripts": {
            "dev": "vite",
            "build": "tsc --noEmit && vite build",
            "preview": "vite preview"
          },
          "dependencies": {
            "react": "^18.3.1",
            "react-dom": "^18.3.1"
          },
          "devDependencies": {
            "@types/react": "^18.3.5",
            "@types/react-dom": "^18.3.0",
            "@vitejs/plugin-react": "^4.3.1",
            "typescript": "^5.5.4",
            "vite": "^5.4.2"
          }
        }
        '''
    )


def _frontend_tsconfig() -> str:
    return _text(
        '''
        {
          "compilerOptions": {
            "target": "ES2020",
            "useDefineForClassFields": true,
            "lib": ["ES2020", "DOM", "DOM.Iterable"],
            "module": "ESNext",
            "moduleResolution": "bundler",
            "resolveJsonModule": true,
            "isolatedModules": true,
            "noEmit": true,
            "jsx": "react-jsx",
            "strict": true,
            "noUnusedLocals": true,
            "noUnusedParameters": true,
            "noFallthroughCasesInSwitch": true,
            "skipLibCheck": true,
            "types": ["vite/client"]
          },
          "include": ["src", "vite.config.ts"]
        }
        '''
    )


def _frontend_index_html() -> str:
    return _text(
        '''
        <!doctype html>
        <html lang="en">
          <head>
            <meta charset="UTF-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1.0" />
            <title>{{project_name}}</title>
          </head>
          <body>
            <div id="root"></div>
            <script type="module" src="/src/main.tsx"></script>
          </body>
        </html>
        '''
    )


def _frontend_main_tsx() -> str:
    return _text(
        '''
        import React from "react";
        import ReactDOM from "react-dom/client";

        import App from "./App";

        const rootElement = document.getElementById("root");
        if (!rootElement) {
          throw new Error("index.html is missing the #root element");
        }

        ReactDOM.createRoot(rootElement).render(
          <React.StrictMode>
            <App />
          </React.StrictMode>,
        );
        '''
    )


def _frontend_vite_env() -> str:
    return '/// <reference types="vite/client" />\n'


def _frontend_vite_config(api_paths: tuple[str, ...]) -> str:
    proxy_lines = "\n".join(
        f'      "{path}": {{ target: "http://localhost:8000", changeOrigin: true }},'
        for path in api_paths
    )
    return _text(
        '''
        import { defineConfig } from "vite";
        import react from "@vitejs/plugin-react";

        export default defineConfig({
          plugins: [react()],
          server: {
            port: 5173,
            proxy: {
        %PROXY%
            },
          },
        });
        '''
    ).replace("%PROXY%", proxy_lines)


def _frontend_nginx_conf() -> str:
    return _text(
        '''
        # Serves the built single page app and proxies API traffic to the backend
        # container on the compose network.
        server {
            listen 80;
            server_name _;
            root /usr/share/nginx/html;
            index index.html;

            location /api/ {
                proxy_pass http://backend:8000;
                proxy_set_header Host $host;
                proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            }

            location /health {
                proxy_pass http://backend:8000/health;
                proxy_set_header Host $host;
            }

            location / {
                try_files $uri $uri/ /index.html;
            }
        }
        '''
    )


def _frontend_dockerfile() -> str:
    return _text(
        '''
        FROM node:20-alpine AS build

        WORKDIR /app

        ARG VITE_API_BASE_URL=""
        ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

        COPY package.json ./
        RUN npm install --no-audit --no-fund

        COPY tsconfig.json vite.config.ts index.html ./
        COPY src ./src
        RUN npm run build

        FROM nginx:1.27-alpine

        COPY nginx.conf /etc/nginx/conf.d/default.conf
        COPY --from=build /app/dist /usr/share/nginx/html

        EXPOSE 80
        HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \\
            CMD wget -q -O /dev/null http://127.0.0.1/index.html || exit 1

        CMD ["nginx", "-g", "daemon off;"]
        '''
    )


def _python_dockerfile(module: str = "app.main:app") -> str:
    """Return a non-root python image that runs uvicorn with *module*."""
    return _text(
        '''
        FROM python:3.12-slim

        ENV PYTHONDONTWRITEBYTECODE=1 \\
            PYTHONUNBUFFERED=1 \\
            PIP_NO_CACHE_DIR=1

        WORKDIR /app

        COPY requirements.txt ./requirements.txt
        RUN pip install --no-cache-dir -r requirements.txt

        COPY app ./app

        RUN useradd --create-home --uid 10001 appuser \\
            && chown -R appuser:appuser /app
        USER appuser

        EXPOSE 8000
        HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \\
            CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

        CMD ["uvicorn", "%MODULE%", "--host", "0.0.0.0", "--port", "8000"]
        '''
    ).replace("%MODULE%", module)


def _requirements_runtime(fastapi: bool = True, extras: tuple[str, ...] = ()) -> str:
    lines = []
    if fastapi:
        lines += ["fastapi==0.115.0", "uvicorn[standard]==0.30.6", "pydantic==2.9.2"]
    lines.extend(extras)
    return "\n".join(lines) + "\n"


def _requirements_dev() -> str:
    return _text(
        '''
        httpx==0.27.2
        pytest==8.3.3
        ruff==0.6.9
        '''
    )


# ---------------------------------------------------------------------------
# react-fastapi
# ---------------------------------------------------------------------------


def _react_fastapi_files() -> dict[str, str]:
    return {
        "README.md": _text(
            '''
            # {{project_name}}

            A React + TypeScript single page app served next to a FastAPI backend,
            wired for containerised deployment.

            ## Stack

            | Layer | Choice |
            | --- | --- |
            | Frontend | React 18, TypeScript and Vite |
            | Backend | FastAPI on Python 3.12 (uvicorn) |
            | Database | none yet - items are held in memory |
            | Tests | pytest + ruff (backend), `tsc --noEmit` and `vite build` (frontend) |
            | Deploy | Docker Compose: nginx serves the SPA, uvicorn runs the API |

            ## Layout

            ```
            backend/app/main.py     FastAPI application (/health, /api/v1/items)
            backend/tests/          pytest suite
            frontend/src/           React entry point and application component
            frontend/nginx.conf     SPA and API reverse proxy for the container
            docker-compose.yml      frontend + backend services
            ```

            ## Run locally

            ```bash
            # terminal 1 - API
            cd backend
            python -m venv .venv && source .venv/bin/activate
            pip install -r requirements.txt -r requirements-dev.txt
            uvicorn app.main:app --reload --port 8000

            # terminal 2 - web app
            cd frontend
            npm install
            npm run dev        # http://localhost:5173, proxies /api and /health to :8000
            ```

            ## Test

            ```bash
            cd backend && ruff check . && pytest -q
            cd frontend && npm run build
            ```

            ## Configuration

            `cp .env.example .env` and fill in the values you need; every variable is
            documented in that file. `.env` is git-ignored and must never be committed.

            ## Deployment

            `docker compose up --build -d` builds `frontend/` (nginx) and `backend/`
            (uvicorn). The SPA is published on port 5173 and the API on 8000; terminate
            TLS in front of the frontend and keep the API off the public internet. The
            backend exposes `GET /health`, which compose and the platform use as the
            liveness/readiness probe. CI (`.github/workflows/ci.yml`) runs ruff, pytest,
            the frontend build and a docker build without publishing an image.
            '''
        ),
        ".gitignore": _gitignore_node(),
        ".env.example": _text(
            '''
            # Copy this file to .env and fill in the values. An empty value means
            # "use the default from docker-compose.yml"; never commit a filled .env.

            # Runtime environment of the API (development | staging | production).
            APP_ENV=
            # Host port that publishes the backend container.
            BACKEND_PORT=
            # Log level for uvicorn and application logs (debug | info | warning | error).
            LOG_LEVEL=
            # Base URL the browser uses to reach the API; empty keeps the Vite dev proxy.
            VITE_API_BASE_URL=
            '''
        ),
        "backend/requirements.txt": _requirements_runtime(),
        "backend/requirements-dev.txt": _requirements_dev(),
        "backend/app/__init__.py": _text(
            '''
            """FastAPI backend for {{project_name}}."""
            '''
        ),
        "backend/app/main.py": _text(
            '''
            """Item API for {{project_name}}."""

            from __future__ import annotations

            import os

            from fastapi import FastAPI
            from pydantic import BaseModel, Field

            SERVICE_NAME = os.getenv("SERVICE_NAME", "{{project_slug}}-backend")

            app = FastAPI(title=SERVICE_NAME, version="0.1.0")


            class Health(BaseModel):
                """Payload returned by the liveness probe."""

                status: str
                service: str


            class Item(BaseModel):
                """A single item."""

                id: int = Field(ge=1)
                name: str = Field(min_length=1, max_length=120)


            class ItemCreate(BaseModel):
                """Payload accepted when creating an item."""

                name: str = Field(min_length=1, max_length=120)


            class ItemList(BaseModel):
                """Envelope so the response can grow without breaking clients."""

                items: list[Item]


            _ITEMS: list[Item] = [Item(id=1, name="first item")]
            _NEXT_ID = 2


            @app.get("/health", response_model=Health)
            def health() -> Health:
                """Liveness/readiness probe used by Docker and the platform."""
                return Health(status="ok", service=SERVICE_NAME)


            @app.get("/api/v1/items", response_model=ItemList)
            def list_items() -> ItemList:
                """Return the in-memory collection (swap in a repository later)."""
                return ItemList(items=list(_ITEMS))


            @app.post("/api/v1/items", response_model=Item, status_code=201)
            def create_item(payload: ItemCreate) -> Item:
                """Append an item to the in-memory store."""
                global _NEXT_ID
                item = Item(id=_NEXT_ID, name=payload.name)
                _NEXT_ID += 1
                _ITEMS.append(item)
                return item
            '''
        ),
        "backend/tests/test_health.py": _text(
            '''
            """Contract tests for the health endpoint and the items API."""

            from __future__ import annotations

            from app.main import app
            from fastapi.testclient import TestClient

            client = TestClient(app)


            def test_health_reports_ok() -> None:
                response = client.get("/health")
                assert response.status_code == 200
                body = response.json()
                assert body["status"] == "ok"
                assert body["service"].endswith("-backend")


            def test_items_endpoint_lists_seeded_item() -> None:
                response = client.get("/api/v1/items")
                assert response.status_code == 200
                names = [item["name"] for item in response.json()["items"]]
                assert "first item" in names


            def test_create_item_returns_created_payload() -> None:
                response = client.post("/api/v1/items", json={"name": "second item"})
                assert response.status_code == 201
                assert response.json()["name"] == "second item"
            '''
        ),
        "backend/Dockerfile": _python_dockerfile(),
        "frontend/package.json": _frontend_package_json(),
        "frontend/tsconfig.json": _frontend_tsconfig(),
        "frontend/vite.config.ts": _frontend_vite_config(("/api", "/health")),
        "frontend/index.html": _frontend_index_html(),
        "frontend/nginx.conf": _frontend_nginx_conf(),
        "frontend/Dockerfile": _frontend_dockerfile(),
        "frontend/src/vite-env.d.ts": _frontend_vite_env(),
        "frontend/src/main.tsx": _frontend_main_tsx(),
        "frontend/src/App.tsx": _text(
            '''
            import { useEffect, useState } from "react";

            type Health = { status: string; service: string };
            type Item = { id: number; name: string };
            type ItemList = { items: Item[] };

            const API_BASE: string = import.meta.env.VITE_API_BASE_URL ?? "";

            export default function App() {
              const [health, setHealth] = useState<Health | null>(null);
              const [items, setItems] = useState<Item[]>([]);
              const [error, setError] = useState<string | null>(null);

              useEffect(() => {
                let cancelled = false;

                async function load(): Promise<void> {
                  try {
                    const [healthResponse, itemsResponse] = await Promise.all([
                      fetch(`${API_BASE}/health`),
                      fetch(`${API_BASE}/api/v1/items`),
                    ]);
                    if (!healthResponse.ok || !itemsResponse.ok) {
                      throw new Error(
                        `API responded with ${healthResponse.status}/${itemsResponse.status}`,
                      );
                    }
                    const healthBody = (await healthResponse.json()) as Health;
                    const itemsBody = (await itemsResponse.json()) as ItemList;
                    if (!cancelled) {
                      setHealth(healthBody);
                      setItems(itemsBody.items);
                      setError(null);
                    }
                  } catch (cause) {
                    if (!cancelled) {
                      setError(cause instanceof Error ? cause.message : "request failed");
                    }
                  }
                }

                void load();
                return () => {
                  cancelled = true;
                };
              }, []);

              return (
                <main>
                  <h1>{{project_name}}</h1>
                  {error !== null && <p role="alert">{error}</p>}
                  <p>
                    API status: <strong>{health === null ? "unknown" : health.status}</strong>
                  </p>
                  <section>
                    <h2>Items</h2>
                    <ul>
                      {items.map((item) => (
                        <li key={item.id}>{item.name}</li>
                      ))}
                    </ul>
                  </section>
                </main>
              );
            }
            '''
        ),
        "docker-compose.yml": _text(
            '''
            # Frontend (nginx) and backend (uvicorn) on one private compose network.
            services:
              backend:
                build:
                  context: ./backend
                environment:
                  APP_ENV: ${APP_ENV:-development}
                  LOG_LEVEL: ${LOG_LEVEL:-info}
                ports:
                  - "${BACKEND_PORT:-8000}:8000"
                healthcheck:
                  test: ["CMD", "python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]
                  interval: 30s
                  timeout: 5s
                  retries: 3
                  start_period: 10s
                restart: unless-stopped
                networks:
                  - app_net

              frontend:
                build:
                  context: ./frontend
                  args:
                    VITE_API_BASE_URL: ${VITE_API_BASE_URL:-}
                ports:
                  - "5173:80"
                depends_on:
                  backend:
                    condition: service_healthy
                restart: unless-stopped
                networks:
                  - app_net

            networks:
              app_net:
                driver: bridge
            '''
        ),
    }


# ---------------------------------------------------------------------------
# fastapi-api
# ---------------------------------------------------------------------------


def _fastapi_api_files() -> dict[str, str]:
    return {
        "README.md": _text(
            '''
            # {{project_name}}

            A single FastAPI service with a typed settings layer and an in-memory
            items API, ready to containerise.

            ## Stack

            | Layer | Choice |
            | --- | --- |
            | Frontend | none - JSON API only |
            | Backend | FastAPI on Python 3.12 (uvicorn) |
            | Database | none yet - items are held in memory |
            | Tests | pytest, httpx and ruff |
            | Deploy | Docker image + Docker Compose |

            ## Layout

            ```
            app/main.py      application factory, /health endpoint
            app/items.py     /api/v1/items router and models
            app/settings.py  environment driven configuration
            tests/           pytest suite (health and items contracts)
            ```

            ## Run locally

            ```bash
            python -m venv .venv && source .venv/bin/activate
            pip install -r requirements.txt -r requirements-dev.txt
            uvicorn app.main:app --reload --port 8000
            ```

            Interactive docs are on `http://localhost:8000/docs`.

            ## Test

            ```bash
            ruff check .
            pytest -q
            ```

            ## Configuration

            `cp .env.example .env` and fill in the values you need; each variable is
            documented in that file. `.env` is git-ignored and must never be committed.

            ## Deployment

            `docker compose up --build -d` builds the image and publishes the API on
            `${HOST_PORT:-8000}`. Terminate TLS at the edge, keep the container off the
            public internet behind that proxy, and point the platform readiness probe at
            `GET /health`, which answers `{"status": "ok", "service": ...}`.
            '''
        ),
        ".gitignore": _gitignore_python(),
        ".env.example": _text(
            '''
            # Copy this file to .env and fill in the values. An empty value means
            # "use the default from docker-compose.yml"; never commit a filled .env.

            # Public name of the service, echoed by /health.
            SERVICE_NAME=
            # Log level for uvicorn and application logs (debug | info | warning | error).
            LOG_LEVEL=
            # Comma separated list of origins allowed by CORS.
            CORS_ORIGINS=
            # Host port that publishes the API container.
            HOST_PORT=
            '''
        ),
        "requirements.txt": _requirements_runtime(),
        "requirements-dev.txt": _requirements_dev(),
        "app/__init__.py": _text(
            '''
            """{{project_name}} API package."""
            '''
        ),
        "app/settings.py": _text(
            '''
            """Environment driven settings, read once at start-up."""

            from __future__ import annotations

            import os
            from dataclasses import dataclass


            @dataclass(frozen=True)
            class Settings:
                """Runtime configuration of the service."""

                service_name: str
                log_level: str
                cors_origins: tuple[str, ...]

                @classmethod
                def from_env(cls) -> Settings:
                    """Build settings from environment variables with local defaults."""
                    raw_origins = os.getenv("CORS_ORIGINS", "http://localhost:5173")
                    return cls(
                        service_name=os.getenv("SERVICE_NAME", "{{project_slug}}-api"),
                        log_level=os.getenv("LOG_LEVEL", "info").strip().lower() or "info",
                        cors_origins=tuple(
                            origin.strip() for origin in raw_origins.split(",") if origin.strip()
                        ),
                    )
            '''
        ),
        "app/items.py": _text(
            '''
            """Items router backed by an in-memory store."""

            from __future__ import annotations

            from fastapi import APIRouter, HTTPException, status
            from pydantic import BaseModel, Field

            router = APIRouter(tags=["items"])


            class Item(BaseModel):
                """A stored item."""

                id: int = Field(ge=1)
                name: str = Field(min_length=1, max_length=120)


            class ItemCreate(BaseModel):
                """Payload accepted when creating an item."""

                name: str = Field(min_length=1, max_length=120)


            _STORE: dict[int, Item] = {1: Item(id=1, name="first item")}
            _NEXT_ID = 2


            @router.get("/items", response_model=list[Item])
            def list_items() -> list[Item]:
                """Return every item, ordered by id."""
                return [_STORE[key] for key in sorted(_STORE)]


            @router.post("/items", response_model=Item, status_code=status.HTTP_201_CREATED)
            def create_item(payload: ItemCreate) -> Item:
                """Create an item in the in-memory store (replace with a repository)."""
                global _NEXT_ID
                item = Item(id=_NEXT_ID, name=payload.name)
                _STORE[item.id] = item
                _NEXT_ID += 1
                return item


            @router.get("/items/{item_id}", response_model=Item)
            def get_item(item_id: int) -> Item:
                """Fetch one item or raise 404."""
                try:
                    return _STORE[item_id]
                except KeyError:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="item not found",
                    ) from None
            '''
        ),
        "app/main.py": _text(
            '''
            """Application entry point for {{project_name}}."""

            from __future__ import annotations

            from fastapi import FastAPI
            from fastapi.middleware.cors import CORSMiddleware

            from app.items import router as items_router
            from app.settings import Settings

            settings = Settings.from_env()


            def create_app() -> FastAPI:
                """Build the application; separate so tests get a fresh instance."""
                application = FastAPI(title=settings.service_name, version="0.1.0")
                application.add_middleware(
                    CORSMiddleware,
                    allow_origins=list(settings.cors_origins),
                    allow_methods=["GET", "POST"],
                    allow_headers=["*"],
                )
                application.include_router(items_router, prefix="/api/v1")

                @application.get("/health")
                def health() -> dict[str, str]:
                    """Liveness/readiness probe: cheap and dependency free."""
                    return {"status": "ok", "service": settings.service_name}

                return application


            app = create_app()
            '''
        ),
        "tests/test_health.py": _text(
            '''
            """Health contract tests."""

            from __future__ import annotations

            from app.main import app
            from fastapi.testclient import TestClient

            client = TestClient(app)


            def test_health_reports_ok() -> None:
                response = client.get("/health")
                assert response.status_code == 200
                body = response.json()
                assert body["status"] == "ok"
                assert body["service"].endswith("-api")


            def test_health_is_exposed_at_root_only() -> None:
                assert client.get("/api/v1/health").status_code == 404
            '''
        ),
        "tests/test_items.py": _text(
            '''
            """Items API contract tests."""

            from __future__ import annotations

            from app.main import app
            from fastapi.testclient import TestClient

            client = TestClient(app)


            def test_list_items_returns_seeded_item() -> None:
                response = client.get("/api/v1/items")
                assert response.status_code == 200
                names = [item["name"] for item in response.json()]
                assert "first item" in names


            def test_create_item_assigns_an_id() -> None:
                response = client.post("/api/v1/items", json={"name": "second item"})
                assert response.status_code == 201
                body = response.json()
                assert body["name"] == "second item"
                assert body["id"] >= 2


            def test_unknown_item_returns_404() -> None:
                response = client.get("/api/v1/items/9999")
                assert response.status_code == 404
                assert response.json()["detail"] == "item not found"


            def test_blank_name_is_rejected() -> None:
                response = client.post("/api/v1/items", json={"name": ""})
                assert response.status_code == 422
            '''
        ),
        "Dockerfile": _python_dockerfile(),
        "docker-compose.yml": _text(
            '''
            # Single service stack; drop the published port and reach it through a
            # reverse proxy when deploying to a shared host.
            services:
              api:
                build:
                  context: .
                environment:
                  SERVICE_NAME: ${SERVICE_NAME:-{{project_slug}}-api}
                  LOG_LEVEL: ${LOG_LEVEL:-info}
                  CORS_ORIGINS: ${CORS_ORIGINS:-http://localhost:5173}
                ports:
                  - "${HOST_PORT:-8000}:8000"
                healthcheck:
                  test: ["CMD", "python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]
                  interval: 30s
                  timeout: 5s
                  retries: 3
                  start_period: 10s
                restart: unless-stopped
            '''
        ),
    }


# ---------------------------------------------------------------------------
# python-agent
# ---------------------------------------------------------------------------


def _python_agent_files() -> dict[str, str]:
    return {
        "README.md": _text(
            '''
            # {{project_name}}

            A small, standard-library-only agent with an explicit and bounded step
            loop: the planner proposes one action at a time and the runner stops as
            soon as the plan is exhausted or the step budget is spent.

            ## Stack

            | Layer | Choice |
            | --- | --- |
            | Frontend | none - console application |
            | Backend | Python 3.12 package (`agent`) with no runtime dependencies |
            | Database | none - state lives in memory for the duration of a run |
            | Tests | pytest and ruff |
            | Deploy | Docker image + Docker Compose, one short-lived container |

            ## Layout

            ```
            agent/config.py   AgentConfig, reads AGENT_GOAL and AGENT_MAX_STEPS
            agent/main.py     planner, executor and the bounded run loop
            tests/test_agent.py
            ```

            ## Run locally

            ```bash
            python -m venv .venv && source .venv/bin/activate
            pip install -r requirements.txt -r requirements-dev.txt
            AGENT_MAX_STEPS=4 AGENT_GOAL="triage the backlog" python -m agent.main
            ```

            The process exits 0 once the goal is reached or the budget is exhausted.

            ## Test

            ```bash
            ruff check .
            pytest -q
            ```

            ## Configuration

            `cp .env.example .env` and fill in the values you need; each variable is
            documented in that file. `.env` is git-ignored and must never be committed.

            ## Deployment

            `docker compose up --build` runs one container and exits with its status; the
            agent is a batch job, not a long-running server, so no port is published.
            Schedule it from cron or a Kubernetes `Job` and alert on a non-zero exit code.
            `AGENT_MAX_STEPS` is the hard ceiling that keeps a bad plan from looping
            forever.

            ## Health

            Two signals, for two different questions:

            - **Did the run succeed?** The container exit code (0 when the plan finished or
              the budget was reached) plus the JSON summary printed on stdout. This is the
              signal to alert on, and the only one that says anything about the work.
            - **Is the supervision wrapper alive?** `python -m agent.health` serves
              `GET /health` -> `{"status": "ok", "service": "...", "max_steps": N}` on
              `AGENT_HEALTH_PORT` (default 8080), for orchestrators that require a /health
              contract before routing traffic or accepting a job. It reports the wrapper,
              never the outcome of a run.
            '''
        ),
        ".gitignore": _gitignore_python(),
        ".env.example": _text(
            '''
            # Copy this file to .env and fill in the values. An empty value means
            # "use the default from docker-compose.yml"; never commit a filled .env.

            # Goal handed to the planner for this run.
            AGENT_GOAL=
            # Hard ceiling on planner steps; the run stops when it is reached.
            AGENT_MAX_STEPS=
            # Log level for the agent (debug | info | warning | error).
            LOG_LEVEL=
            # Port served by `python -m agent.health` (empty = 8080, probe disabled unless started).
            AGENT_HEALTH_PORT=
            '''
        ),
        "requirements.txt": _text(
            '''
            # The agent runtime uses the Python standard library only.
            # Pin runtime dependencies here as soon as one is introduced.
            '''
        ),
        "requirements-dev.txt": _text(
            '''
            pytest==8.3.3
            ruff==0.6.9
            '''
        ),
        "agent/__init__.py": _text(
            '''
            """Bounded planning agent for {{project_name}}.

            Import :class:`agent.config.AgentConfig` for configuration and
            ``agent.main.run`` for the loop; the package itself stays import cheap.
            """

            __all__ = ["config", "main"]
            '''
        ),
        "agent/config.py": _text(
            '''
            """Environment driven agent configuration."""

            from __future__ import annotations

            import os
            from dataclasses import dataclass

            DEFAULT_MAX_STEPS = 5


            def _positive_int(name: str, default: int) -> int:
                """Read a strictly positive integer from the environment."""
                raw = os.getenv(name)
                if raw is None or not raw.strip():
                    return default
                try:
                    value = int(raw)
                except ValueError as exc:
                    raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
                if value < 1:
                    raise ValueError(f"{name} must be >= 1, got {value}")
                return value


            @dataclass(frozen=True)
            class AgentConfig:
                """Everything the run loop needs."""

                goal: str
                max_steps: int = DEFAULT_MAX_STEPS

                @classmethod
                def from_env(cls) -> AgentConfig:
                    """Build a config from AGENT_GOAL and AGENT_MAX_STEPS."""
                    return cls(
                        goal=os.getenv("AGENT_GOAL", "summarise the open backlog").strip(),
                        max_steps=_positive_int("AGENT_MAX_STEPS", DEFAULT_MAX_STEPS),
                    )
            '''
        ),
        "agent/health.py": _text(
            '''
            """Opt-in HTTP health probe for orchestration that requires a /health contract.

            A batch run has no long-running process to probe, so this probe is a separate,
            explicit mode: start ``python -m agent.health`` next to the job (supervisor,
            sidecar, or compose ``depends_on``) and point the platform at AGENT_HEALTH_PORT.
            It reports liveness of the supervision wrapper, never the outcome of a run.
            """

            from __future__ import annotations

            import json
            import os
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

            from agent.config import AgentConfig

            SERVICE = "{{project_slug}}"


            class _HealthHandler(BaseHTTPRequestHandler):
                """Answers GET /health with a small JSON document; 404 for anything else."""

                def do_GET(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
                    if self.path.split("?")[0] != "/health":
                        self.send_error(404, "not found")
                        return
                    config = AgentConfig.from_env()
                    body = json.dumps(
                        {"status": "ok", "service": SERVICE, "max_steps": config.max_steps}
                    ).encode()
                    self.send_response(200)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, fmt: str, *args: object) -> None:
                    """Silence per-request logging: probes would spam the job output."""


            def serve(port: int | None = None) -> None:
                """Serve the probe on AGENT_HEALTH_PORT (default 8080) until interrupted."""
                resolved = port if port is not None else int(os.getenv("AGENT_HEALTH_PORT", "8080"))
                server = ThreadingHTTPServer(("0.0.0.0", resolved), _HealthHandler)
                try:
                    server.serve_forever()
                finally:
                    server.server_close()


            if __name__ == "__main__":
                serve()
            '''
        ),
        "tests/test_health.py": _text(
            '''
            """The opt-in probe answers /health and nothing else."""

            from __future__ import annotations

            import json
            import threading
            import urllib.error
            import urllib.request
            from http.server import ThreadingHTTPServer

            from agent.health import _HealthHandler


            def _serve_ephemeral():
                server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                return server, server.server_address[1]


            def test_health_reports_ok() -> None:
                server, port = _serve_ephemeral()
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as response:
                        assert response.status == 200
                        body = json.loads(response.read())
                finally:
                    server.shutdown()
                    server.server_close()
                assert body["status"] == "ok"
                assert body["service"] == "{{project_slug}}"


            def test_unknown_path_is_not_healthy() -> None:
                server, port = _serve_ephemeral()
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/ready", timeout=5):
                        raise AssertionError("expected a 404")
                except urllib.error.HTTPError as error:
                    assert error.code == 404
                finally:
                    server.shutdown()
                    server.server_close()
            '''
        ),
        "agent/main.py": _text(
            '''
            """Planner, executor and the bounded run loop."""

            from __future__ import annotations

            import json
            import logging
            import os
            from dataclasses import dataclass, field

            from agent.config import AgentConfig

            LOGGER = logging.getLogger("agent")


            @dataclass
            class AgentState:
                """Mutable state carried across planner steps."""

                goal: str
                step: int = 0
                findings: list[str] = field(default_factory=list)


            def plan(state: AgentState) -> str | None:
                """Return the next action, or None once the goal is satisfied."""
                if state.step == 0:
                    return "inspect"
                if state.step == 1:
                    return "act"
                if state.step == 2:
                    return "report"
                return None


            def execute(action: str, state: AgentState) -> None:
                """Apply one action to the state (the only place with side effects)."""
                state.findings.append(f"{action}: done for {state.goal}")


            def run(config: AgentConfig) -> AgentState:
                """Run until the plan is exhausted or ``max_steps`` is reached."""
                state = AgentState(goal=config.goal)
                while state.step < config.max_steps:
                    action = plan(state)
                    if action is None:
                        LOGGER.info("goal satisfied after %d step(s)", state.step)
                        break
                    execute(action, state)
                    state.step += 1
                else:
                    LOGGER.warning("step budget of %d exhausted", config.max_steps)
                return state


            def main() -> int:
                """Console entry point: ``python -m agent.main``."""
                logging.basicConfig(
                    level=os.getenv("LOG_LEVEL", "info").upper(),
                    format="%(levelname)s %(name)s %(message)s",
                )
                config = AgentConfig.from_env()
                state = run(config)
                print(
                    json.dumps(
                        {
                            "goal": state.goal,
                            "steps": state.step,
                            "findings": state.findings,
                        }
                    )
                )
                return 0


            if __name__ == "__main__":
                raise SystemExit(main())
            '''
        ),
        "tests/test_agent.py": _text(
            '''
            """Tests for the bounded run loop and the environment config."""

            from __future__ import annotations

            import pytest
            from agent.config import AgentConfig
            from agent.main import plan, run


            def test_run_stops_at_the_step_budget() -> None:
                state = run(AgentConfig(goal="triage the backlog", max_steps=2))
                assert state.step == 2
                assert len(state.findings) == 2
                assert state.findings[0] == "inspect: done for triage the backlog"


            def test_run_finishes_early_when_the_plan_is_exhausted() -> None:
                state = run(AgentConfig(goal="triage the backlog", max_steps=10))
                assert state.step == 3
                assert [finding.split(":")[0] for finding in state.findings] == [
                    "inspect",
                    "act",
                    "report",
                ]


            def test_plan_returns_none_once_every_step_ran() -> None:
                state = run(AgentConfig(goal="noop", max_steps=3))
                assert plan(state) is None


            def test_config_reads_the_budget_from_the_environment(
                monkeypatch: pytest.MonkeyPatch,
            ) -> None:
                monkeypatch.setenv("AGENT_GOAL", "summarise incidents")
                monkeypatch.setenv("AGENT_MAX_STEPS", "7")
                config = AgentConfig.from_env()
                assert config.goal == "summarise incidents"
                assert config.max_steps == 7


            def test_config_rejects_a_non_numeric_budget(monkeypatch: pytest.MonkeyPatch) -> None:
                monkeypatch.setenv("AGENT_MAX_STEPS", "many")
                with pytest.raises(ValueError, match="must be an integer"):
                    AgentConfig.from_env()


            def test_config_rejects_a_non_positive_budget(monkeypatch: pytest.MonkeyPatch) -> None:
                monkeypatch.setenv("AGENT_MAX_STEPS", "0")
                with pytest.raises(ValueError, match="must be >= 1"):
                    AgentConfig.from_env()
            '''
        ),
        "Dockerfile": _text(
            '''
            FROM python:3.12-slim

            ENV PYTHONDONTWRITEBYTECODE=1 \\
                PYTHONUNBUFFERED=1 \\
                PIP_NO_CACHE_DIR=1

            WORKDIR /app

            COPY requirements.txt ./requirements.txt
            RUN pip install --no-cache-dir -r requirements.txt

            COPY agent ./agent

            RUN useradd --create-home --uid 10001 agentuser \\
                && chown -R agentuser:agentuser /app
            USER agentuser

            CMD ["python", "-m", "agent.main"]
            '''
        ),
        "docker-compose.yml": _text(
            '''
            # One short-lived container: the agent runs a bounded loop and exits.
            services:
              agent:
                build:
                  context: .
                environment:
                  AGENT_GOAL: ${AGENT_GOAL:-summarise the open backlog}
                  AGENT_MAX_STEPS: ${AGENT_MAX_STEPS:-5}
                  LOG_LEVEL: ${LOG_LEVEL:-info}
                restart: "no"
            '''
        ),
    }


# ---------------------------------------------------------------------------
# react-fastapi-rag
# ---------------------------------------------------------------------------


def _rag_files() -> dict[str, str]:
    return {
        "README.md": _text(
            '''
            # {{project_name}}

            A retrieval augmented search starter: React + TypeScript front end, a
            FastAPI service and PostgreSQL with the pgvector extension.

            ## Stack

            | Layer | Choice |
            | --- | --- |
            | Frontend | React 18, TypeScript and Vite |
            | Backend | FastAPI on Python 3.12 (uvicorn) with psycopg |
            | Database | PostgreSQL 16 + pgvector, internal compose network only |
            | Tests | pytest + ruff (backend), `tsc --noEmit` and `vite build` (frontend) |
            | Deploy | Docker Compose: nginx, uvicorn and a named-volume database |

            ## Layout

            ```
            backend/app/main.py   /health and /api/v1/search
            backend/app/db.py     lazy psycopg connection helper
            db/schema.sql         documents and chunks tables, vector index
            frontend/src/App.tsx  search form and result list
            docker-compose.yml    frontend + backend + db
            ```

            ## Run locally

            ```bash
            cp .env.example .env      # fill POSTGRES_* and DATABASE_URL
            docker compose up --build -d
            curl http://localhost:8000/health
            ```

            The database is created from `db/schema.sql` on first start. To run the API
            outside a container, export `DATABASE_URL` and use:

            ```bash
            cd backend
            python -m venv .venv && source .venv/bin/activate
            pip install -r requirements.txt -r requirements-dev.txt
            uvicorn app.main:app --reload --port 8000
            ```

            ## Test

            ```bash
            cd backend && ruff check . && pytest -q
            cd frontend && npm run build
            ```

            ## Configuration

            `cp .env.example .env` and fill in every value; each variable is documented
            in that file. `.env` is git-ignored and must never be committed.

            ## Deployment

            `docker compose up --build -d` starts frontend (port 5173), API (port 8000)
            and Postgres. The database has **no published port** - it is reachable only
            from containers on the internal network - and its data lives in the named
            volume `pgdata`, so `docker compose down` keeps it while `down -v` drops it.
            Publish only the frontend behind a TLS proxy; `GET /health` is the readiness
            probe for the API and the platform.
            '''
        ),
        ".gitignore": _gitignore_node(),
        ".env.example": _text(
            '''
            # Copy this file to .env and fill in every value before starting the stack.
            # The database refuses to start without POSTGRES_* and the API without
            # DATABASE_URL, so an unfilled .env fails fast instead of guessing.
            # Never commit a filled .env: it is git-ignored for a reason.

            # Name of the application database created by the postgres container.
            POSTGRES_DB=
            # Database role owned by the API.
            POSTGRES_USER=
            # Password for that role; generate one locally, e.g. `openssl rand -base64 24`.
            POSTGRES_PASSWORD=
            # psycopg DSN pointing at the internal `db` service, for example
            # postgresql://user:password@db:5432/rag - the password must match above.
            DATABASE_URL=
            # Host port that publishes the frontend container.
            FRONTEND_PORT=
            # Log level for uvicorn and application logs (debug | info | warning | error).
            LOG_LEVEL=
            '''
        ),
        "backend/requirements.txt": _requirements_runtime(
            extras=("psycopg[binary]==3.2.3",),
        ),
        "backend/requirements-dev.txt": _requirements_dev(),
        "backend/app/__init__.py": _text(
            '''
            """Retrieval API package for {{project_name}}."""
            '''
        ),
        "backend/app/settings.py": _text(
            '''
            """Environment driven settings for the retrieval API."""

            from __future__ import annotations

            import os
            from dataclasses import dataclass


            @dataclass(frozen=True)
            class Settings:
                """Runtime configuration of the service."""

                service_name: str
                log_level: str
                database_url: str

                @classmethod
                def from_env(cls) -> Settings:
                    """Build settings from the environment; no credentials are defaulted."""
                    return cls(
                        service_name=os.getenv("SERVICE_NAME", "{{project_slug}}-rag"),
                        log_level=os.getenv("LOG_LEVEL", "info").strip().lower() or "info",
                        database_url=os.getenv("DATABASE_URL", "").strip(),
                    )
            '''
        ),
        "backend/app/db.py": _text(
            '''
            """Lazy PostgreSQL access; importing this module never opens a connection."""

            from __future__ import annotations

            import os
            from typing import Any


            def database_url() -> str:
                """Return the configured DSN, or an empty string when unset."""
                return os.getenv("DATABASE_URL", "").strip()


            def database_configured() -> bool:
                """True when a DSN is present; /health reports this without connecting."""
                return bool(database_url())


            def connect() -> Any:
                """Open a psycopg connection; the caller owns and closes it."""
                dsn = database_url()
                if not dsn:
                    raise RuntimeError("DATABASE_URL is not set")
                import psycopg

                return psycopg.connect(dsn)
            '''
        ),
        "backend/app/main.py": _text(
            '''
            """Search API for {{project_name}}."""

            from __future__ import annotations

            from fastapi import FastAPI, Query
            from pydantic import BaseModel, Field

            from app.db import database_configured
            from app.settings import Settings

            settings = Settings.from_env()


            class SearchHit(BaseModel):
                """One retrieved chunk."""

                document_id: int
                chunk_id: int
                score: float = Field(ge=0.0, le=1.0)
                content: str


            class SearchResponse(BaseModel):
                """Stable response contract consumed by the React client."""

                query: str
                results: list[SearchHit]
                database_configured: bool


            def _search(query: str) -> SearchResponse:
                """Placeholder retrieval: embed, then query pgvector and rank chunks."""
                return SearchResponse(
                    query=query,
                    results=[],
                    database_configured=database_configured(),
                )


            def create_app() -> FastAPI:
                """Build the application; separate so tests get a fresh instance."""
                application = FastAPI(title=settings.service_name, version="0.1.0")

                @application.get("/health")
                def health() -> dict[str, object]:
                    """Liveness/readiness probe; never opens a database connection."""
                    return {
                        "status": "ok",
                        "service": settings.service_name,
                        "database_configured": database_configured(),
                    }

                @application.get("/api/v1/search", response_model=SearchResponse)
                def search(
                    query: str = Query(min_length=1, max_length=500),
                    limit: int = Query(default=5, ge=1, le=20),
                ) -> SearchResponse:
                    """Search the knowledge base; ``limit`` is honoured once wired up."""
                    del limit  # reserved for the real vector query
                    return _search(query)

                @application.post("/api/v1/search", response_model=SearchResponse)
                def search_post(payload: dict[str, str]) -> SearchResponse:
                    """JSON body variant so clients can send longer queries."""
                    query = str(payload.get("query", "")).strip()
                    if not query:
                        return SearchResponse(
                            query="",
                            results=[],
                            database_configured=database_configured(),
                        )
                    return _search(query)

                return application


            app = create_app()
            '''
        ),
        "backend/tests/test_health.py": _text(
            '''
            """Health contract tests."""

            from __future__ import annotations

            from app.main import app
            from fastapi.testclient import TestClient

            client = TestClient(app)


            def test_health_reports_ok_without_a_database() -> None:
                response = client.get("/health")
                assert response.status_code == 200
                body = response.json()
                assert body["status"] == "ok"
                assert body["service"].endswith("-rag")
                assert body["database_configured"] is False
            '''
        ),
        "backend/tests/test_search.py": _text(
            '''
            """Search stub contract tests."""

            from __future__ import annotations

            from app.main import app
            from fastapi.testclient import TestClient

            client = TestClient(app)


            def test_search_returns_the_query_and_an_empty_result_set() -> None:
                response = client.get("/api/v1/search", params={"query": "deploy"})
                assert response.status_code == 200
                body = response.json()
                assert body["query"] == "deploy"
                assert body["results"] == []
                assert body["database_configured"] is False


            def test_search_rejects_an_empty_query() -> None:
                assert client.get("/api/v1/search", params={"query": ""}).status_code == 422


            def test_search_rejects_an_out_of_range_limit() -> None:
                response = client.get("/api/v1/search", params={"query": "x", "limit": 99})
                assert response.status_code == 422


            def test_search_accepts_a_json_body() -> None:
                response = client.post("/api/v1/search", json={"query": "vectors"})
                assert response.status_code == 200
                assert response.json()["query"] == "vectors"
            '''
        ),
        "backend/Dockerfile": _python_dockerfile(),
        "db/schema.sql": _text(
            '''
            -- Schema for the retrieval store. Applied once by the postgres container
            -- via /docker-entrypoint-initdb.d when the pgdata volume is created.
            CREATE EXTENSION IF NOT EXISTS vector;

            CREATE TABLE IF NOT EXISTS documents (
                id BIGSERIAL PRIMARY KEY,
                source TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );

            CREATE TABLE IF NOT EXISTS chunks (
                id BIGSERIAL PRIMARY KEY,
                document_id BIGINT NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
                content TEXT NOT NULL,
                embedding vector(1536),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (document_id, ordinal)
            );

            CREATE INDEX IF NOT EXISTS chunks_document_id_idx ON chunks (document_id);
            CREATE INDEX IF NOT EXISTS chunks_embedding_idx
                ON chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
            '''
        ),
        "frontend/package.json": _frontend_package_json(),
        "frontend/tsconfig.json": _frontend_tsconfig(),
        "frontend/vite.config.ts": _frontend_vite_config(("/api", "/health")),
        "frontend/index.html": _frontend_index_html(),
        "frontend/nginx.conf": _frontend_nginx_conf(),
        "frontend/Dockerfile": _frontend_dockerfile(),
        "frontend/src/vite-env.d.ts": _frontend_vite_env(),
        "frontend/src/main.tsx": _frontend_main_tsx(),
        "frontend/src/App.tsx": _text(
            '''
            import { useEffect, useState } from "react";
            import type { FormEvent } from "react";

            type SearchHit = {
              document_id: number;
              chunk_id: number;
              score: number;
              content: string;
            };

            type SearchResponse = {
              query: string;
              results: SearchHit[];
              database_configured: boolean;
            };

            const API_BASE: string = import.meta.env.VITE_API_BASE_URL ?? "";

            export default function App() {
              const [query, setQuery] = useState("how do I deploy this service?");
              const [response, setResponse] = useState<SearchResponse | null>(null);
              const [error, setError] = useState<string | null>(null);

              useEffect(() => {
                let cancelled = false;
                fetch(`${API_BASE}/health`)
                  .then((res) => {
                    if (!res.ok) {
                      throw new Error(`health check failed with ${res.status}`);
                    }
                    return res.json();
                  })
                  .catch((cause: unknown) => {
                    if (!cancelled) {
                      setError(cause instanceof Error ? cause.message : "health check failed");
                    }
                  });
                return () => {
                  cancelled = true;
                };
              }, []);

              async function onSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
                event.preventDefault();
                try {
                  const endpoint = `${API_BASE}/api/v1/search?query=${encodeURIComponent(query)}`;
                  const res = await fetch(endpoint);
                  if (!res.ok) {
                    throw new Error(`search failed with ${res.status}`);
                  }
                  setResponse((await res.json()) as SearchResponse);
                  setError(null);
                } catch (cause) {
                  setError(cause instanceof Error ? cause.message : "search failed");
                }
              }

              return (
                <main>
                  <h1>{{project_name}}</h1>
                  <form onSubmit={onSubmit}>
                    <label htmlFor="query">Ask the knowledge base</label>
                    <input
                      id="query"
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                    />
                    <button type="submit">Search</button>
                  </form>
                  {error !== null && <p role="alert">{error}</p>}
                  {response !== null && (
                    <section>
                      <p>
                        {response.results.length} hit(s) for "{response.query}".
                      </p>
                      <ul>
                        {response.results.map((hit) => (
                          <li key={`${hit.document_id}-${hit.chunk_id}`}>{hit.content}</li>
                        ))}
                      </ul>
                    </section>
                  )}
                </main>
              );
            }
            '''
        ),
        "docker-compose.yml": _text(
            '''
            # Frontend + API + Postgres.
            #
            # The database is attached to an internal network only and has no `ports:`
            # entry, so it is never published on the host; it is reachable solely from
            # containers on that network and its data lives in the `pgdata` volume.
            services:
              db:
                image: pgvector/pgvector:pg16
                environment:
                  POSTGRES_DB: ${POSTGRES_DB:?set POSTGRES_DB in .env}
                  POSTGRES_USER: ${POSTGRES_USER:?set POSTGRES_USER in .env}
                  POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}
                volumes:
                  - pgdata:/var/lib/postgresql/data
                  - ./db/schema.sql:/docker-entrypoint-initdb.d/01-schema.sql:ro
                healthcheck:
                  test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
                  interval: 10s
                  timeout: 5s
                  retries: 10
                  start_period: 20s
                restart: unless-stopped
                networks:
                  - internal

              backend:
                build:
                  context: ./backend
                environment:
                  DATABASE_URL: ${DATABASE_URL:?set DATABASE_URL in .env}
                  LOG_LEVEL: ${LOG_LEVEL:-info}
                ports:
                  - "8000:8000"
                depends_on:
                  db:
                    condition: service_healthy
                healthcheck:
                  test: ["CMD", "python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]
                  interval: 30s
                  timeout: 5s
                  retries: 3
                  start_period: 20s
                restart: unless-stopped
                networks:
                  - internal
                  - edge

              frontend:
                build:
                  context: ./frontend
                  args:
                    VITE_API_BASE_URL: ${VITE_API_BASE_URL:-}
                ports:
                  - "${FRONTEND_PORT:-5173}:80"
                depends_on:
                  backend:
                    condition: service_healthy
                restart: unless-stopped
                networks:
                  - edge

            volumes:
              pgdata:

            networks:
              internal:
                driver: bridge
                internal: true
              edge:
                driver: bridge
            '''
        ),
    }


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProjectTemplate:
    """A versioned project scaffold."""

    key: str
    version: str
    name: str
    description: str
    stack: Mapping[str, str]
    metadata: dict[str, object]

    def files(self) -> dict[str, str]:
        """Return the scaffold as ``path -> content``, including the CI workflow."""
        builder = _BUILDERS.get(self.key)
        if builder is None:
            raise KeyError(f"no file builder registered for template {self.key!r}")
        contents = builder()
        contents[CI_WORKFLOW_PATH] = generate_ci(self.key, PROJECT_NAME_TOKEN)
        return contents


_BUILDERS: dict[str, Callable[[], dict[str, str]]] = {
    "react-fastapi": _react_fastapi_files,
    "fastapi-api": _fastapi_api_files,
    "python-agent": _python_agent_files,
    "react-fastapi-rag": _rag_files,
}


TEMPLATE_CATALOGUE: tuple[ProjectTemplate, ...] = (
    ProjectTemplate(
        key="react-fastapi",
        version="1.0.0",
        name="React + FastAPI",
        description="React/TypeScript SPA on a FastAPI backend, served by nginx next to uvicorn in Docker Compose.",
        stack={
            "frontend": "React 18 + TypeScript + Vite",
            "backend": "FastAPI (Python 3.12)",
            "database": "none (in-memory items store)",
            "tests": "pytest, httpx, ruff, tsc + vite build",
            "deploy": "Docker Compose (frontend nginx + backend uvicorn)",
        },
        metadata={
            "has_frontend": True,
            "default_python": "3.12",
            "default_node": "20",
            "services": ["frontend", "backend"],
            "entrypoint": "backend/app/main.py",
            "health_path": "/health",
        },
    ),
    ProjectTemplate(
        key="fastapi-api",
        version="1.0.0",
        name="FastAPI API",
        description="Single FastAPI service with typed settings, items router and a health endpoint.",
        stack={
            "frontend": "none (JSON API)",
            "backend": "FastAPI (Python 3.12)",
            "database": "none (in-memory items store)",
            "tests": "pytest, httpx, ruff",
            "deploy": "Docker image + Docker Compose",
        },
        metadata={
            "has_frontend": False,
            "default_python": "3.12",
            "default_node": "20",
            "services": ["api"],
            "entrypoint": "app/main.py",
            "health_path": "/health",
        },
    ),
    ProjectTemplate(
        key="python-agent",
        version="1.0.0",
        name="Python Agent",
        description="Standard-library agent with an explicit planner and a step budget read from AGENT_MAX_STEPS.",
        stack={
            "frontend": "none (console application)",
            "backend": "Python 3.12 package (stdlib only)",
            "database": "none (in-memory run state)",
            "tests": "pytest, ruff",
            "deploy": "Docker image + Docker Compose batch container",
        },
        metadata={
            "has_frontend": False,
            "default_python": "3.12",
            "default_node": "20",
            "services": ["agent"],
            "entrypoint": "agent/main.py",
            "health_path": "",
            "max_steps_env": "AGENT_MAX_STEPS",
        },
    ),
    ProjectTemplate(
        key="react-fastapi-rag",
        version="1.0.0",
        name="React + FastAPI RAG",
        description="React SPA, FastAPI retrieval API and PostgreSQL with pgvector on an internal compose network.",
        stack={
            "frontend": "React 18 + TypeScript + Vite",
            "backend": "FastAPI (Python 3.12) + psycopg",
            "database": "PostgreSQL 16 + pgvector (internal network, named volume)",
            "tests": "pytest, httpx, ruff, tsc + vite build",
            "deploy": "Docker Compose (frontend nginx + backend uvicorn + postgres)",
        },
        metadata={
            "has_frontend": True,
            "default_python": "3.12",
            "default_node": "20",
            "services": ["frontend", "backend", "db"],
            "entrypoint": "backend/app/main.py",
            "health_path": "/health",
            "database_image": "pgvector/pgvector:pg16",
            "volume": "pgdata",
        },
    ),
)


TEMPLATE_VERSIONS: dict[str, dict[str, ProjectTemplate]] = {}
for _template in TEMPLATE_CATALOGUE:
    TEMPLATE_VERSIONS.setdefault(_template.key, {})[_template.version] = _template


def _version_key(version: str) -> tuple[int, ...]:
    """Order semantic versions without pulling in a packaging dependency."""
    parts = []
    for piece in version.split("."):
        digits = "".join(char for char in piece if char.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def get_template(key: str, version: str | None = None) -> ProjectTemplate:
    """Return a template by key, defaulting to the highest available version."""
    versions = TEMPLATE_VERSIONS.get(key)
    if not versions:
        raise KeyError(f"unknown template {key!r}; known templates: {sorted(TEMPLATE_VERSIONS)}")
    if version is None:
        return max(versions.values(), key=lambda item: _version_key(item.version))
    try:
        return versions[version]
    except KeyError:
        raise KeyError(
            f"unknown version {version!r} for template {key!r}; known versions: {sorted(versions)}"
        ) from None


# ---------------------------------------------------------------------------
# Path validation and materialisation
# ---------------------------------------------------------------------------


def _is_forbidden_name(name: str) -> bool:
    """Reject real dotenv files and anything that looks like a secret payload."""
    lowered = name.lower()
    if lowered == ".env" or (lowered.startswith(".env.") and lowered != ".env.example"):
        return True
    return lowered.startswith("secrets")


def validate_paths(files: Mapping[str, str]) -> None:
    """Validate a ``path -> content`` mapping before it is written to disk.

    Raises:
        TypeError: when *files* is not a mapping of strings to strings.
        ValueError: on absolute paths, ``..`` segments, backslashes, forbidden
            file names or empty content for a code file.
    """
    if not isinstance(files, Mapping):
        raise TypeError("files must be a mapping of path to content")
    for path, content in files.items():
        if not isinstance(path, str) or not path:
            raise ValueError(f"invalid path {path!r}: paths must be non-empty strings")
        if not isinstance(content, str):
            raise TypeError(f"invalid content for {path!r}: content must be a string")
        if path.startswith("/"):
            raise ValueError(f"absolute paths are not allowed: {path!r}")
        if "\\" in path:
            raise ValueError(f"backslashes are not allowed in paths: {path!r}")
        segments = path.split("/")
        if any(segment == ".." for segment in segments):
            raise ValueError(f"path traversal is not allowed: {path!r}")
        for segment in segments:
            if _is_forbidden_name(segment):
                raise ValueError(f"refusing to emit a credential file: {path!r}")
        name = segments[-1]
        suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if not content.strip() and suffix in _CODE_SUFFIXES:
            raise ValueError(f"empty content for code file: {path!r}")


def materialise(
    template_key: str,
    project_name: str,
    substitutions: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Render a template into a writable ``path -> content`` mapping.

    Args:
        template_key: key of a template in :data:`TEMPLATE_CATALOGUE`.
        project_name: human readable project name; it must be a single line.
        substitutions: extra ``{{token}}`` replacements; the two built-in tokens
            cannot be overridden.

    Raises:
        KeyError: when the template key is unknown.
        TypeError: when *project_name* or a substitution is not a string.
        ValueError: on an unusable project name, a protected substitution or an
            unsafe path produced by the template.
    """
    get_template(template_key)
    if not isinstance(project_name, str):
        raise TypeError("project_name must be a string")
    if not project_name.strip():
        raise ValueError("project_name must be a non-empty string")
    name = project_name.strip()
    if "\n" in name or "\r" in name:
        raise ValueError("project_name must be a single line")

    replacements: dict[str, str] = {
        PROJECT_NAME_TOKEN: name,
        PROJECT_SLUG_TOKEN: _slugify(name),
    }
    for token, value in (substitutions or {}).items():
        if not isinstance(token, str) or not isinstance(value, str):
            raise TypeError("substitutions must map strings to strings")
        if token in replacements:
            raise ValueError(f"cannot override the built-in token {token!r}")
        replacements[token] = value

    files: dict[str, str] = {}
    for path, content in get_template(template_key).files().items():
        rendered = content
        for token, value in replacements.items():
            rendered = rendered.replace(token, value)
        files[path] = rendered

    files[CI_WORKFLOW_PATH] = generate_ci(template_key, name)
    validate_paths(files)
    return files


# ---------------------------------------------------------------------------
# CI generation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _CiSpec:
    """Where the build inputs live for one template."""

    python_dir: str
    docker_context: str
    dockerfile: str
    has_frontend: bool
    compose_env: tuple[str, ...] = ()


_CI_SPECS: dict[str, _CiSpec] = {
    "react-fastapi": _CiSpec(
        python_dir="backend",
        docker_context="./backend",
        dockerfile="backend/Dockerfile",
        has_frontend=True,
    ),
    "fastapi-api": _CiSpec(
        python_dir="",
        docker_context=".",
        dockerfile="Dockerfile",
        has_frontend=False,
    ),
    "python-agent": _CiSpec(
        python_dir="",
        docker_context=".",
        dockerfile="Dockerfile",
        has_frontend=False,
    ),
    "react-fastapi-rag": _CiSpec(
        python_dir="backend",
        docker_context="./backend",
        dockerfile="backend/Dockerfile",
        has_frontend=True,
        compose_env=("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "DATABASE_URL"),
    ),
}

_VERSION_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+)*")


def generate_ci(
    template_key: str,
    project_name: str,
    python_version: str = "3.12",
    node_version: str = "20",
) -> str:
    """Return a GitHub Actions workflow for *template_key* honouring its layout.

    The workflow lints and tests the Python code, builds the frontend when the
    template has one, builds the container image without pushing it, and
    validates the compose file. It runs with ``contents: read`` and uses no
    repository secrets.
    """
    template = get_template(template_key)
    spec = _CI_SPECS[template.key]
    for label, value in (("python_version", python_version), ("node_version", node_version)):
        if not isinstance(value, str) or not _VERSION_PATTERN.fullmatch(value):
            raise ValueError(f"{label} must look like 3.12 or 20, got {value!r}")
    if not isinstance(project_name, str) or not project_name.strip():
        raise ValueError("project_name must be a non-empty string")

    slug = _slugify(project_name)
    cache_path = f"{spec.python_dir}/requirements.txt" if spec.python_dir else "requirements.txt"

    lines: list[str] = [
        "name: CI",
        "",
        "on:",
        "  push:",
        "    branches: [main]",
        "  pull_request:",
        "",
        "permissions:",
        "  contents: read",
        "",
        "concurrency:",
        "  group: ci-${{ github.workflow }}-${{ github.ref }}",
        "  cancel-in-progress: true",
        "",
        "jobs:",
        "  python:",
        "    name: Python lint and tests",
        "    runs-on: ubuntu-latest",
    ]
    if spec.python_dir:
        lines += [
            "    defaults:",
            "      run:",
            f"        working-directory: {spec.python_dir}",
        ]
    lines += [
        "    steps:",
        "      - name: Check out the repository",
        "        uses: actions/checkout@v4",
        "      - name: Set up Python",
        "        uses: actions/setup-python@v5",
        "        with:",
        f'          python-version: "{python_version}"',
        "          cache: pip",
        f"          cache-dependency-path: {cache_path}",
        "      - name: Install dependencies",
        "        run: pip install -r requirements.txt -r requirements-dev.txt",
        "      - name: Lint with ruff",
        "        run: ruff check .",
        "      - name: Run tests with pytest",
        "        run: pytest -q",
        "",
    ]

    if spec.has_frontend:
        lines += [
            "  frontend:",
            "    name: Frontend build",
            "    runs-on: ubuntu-latest",
            "    defaults:",
            "      run:",
            "        working-directory: frontend",
            "    steps:",
            "      - name: Check out the repository",
            "        uses: actions/checkout@v4",
            "      - name: Set up Node",
            "        uses: actions/setup-node@v4",
            "        with:",
            f'          node-version: "{node_version}"',
            "      - name: Install dependencies",
            "        run: npm install --no-audit --no-fund",
            "      - name: Build the single page app",
            "        run: npm run build",
            "",
        ]

    needs = "[python, frontend]" if spec.has_frontend else "[python]"
    lines += [
        "  docker:",
        "    name: Container build",
        "    runs-on: ubuntu-latest",
        f"    needs: {needs}",
        "    steps:",
        "      - name: Check out the repository",
        "        uses: actions/checkout@v4",
        "      - name: Set up Docker Buildx",
        "        uses: docker/setup-buildx-action@v3",
        "      - name: Build the image without pushing",
        "        uses: docker/build-push-action@v6",
        "        with:",
        f"          context: {spec.docker_context}",
        f"          file: {spec.dockerfile}",
        "          push: false",
        "          load: true",
        f"          tags: {slug}:ci",
        "          cache-from: type=gha",
        "          cache-to: type=gha,mode=max",
        "      - name: Validate the compose configuration",
    ]
    if spec.compose_env:
        lines.append("        env:")
        for variable in spec.compose_env:
            lines.append(f"          {variable}: ci-placeholder")
    lines.append("        run: docker compose -f docker-compose.yml config --quiet")

    return "\n".join(lines) + "\n"


__all__ = [
    "CI_WORKFLOW_PATH",
    "PROJECT_NAME_TOKEN",
    "PROJECT_SLUG_TOKEN",
    "TEMPLATE_CATALOGUE",
    "TEMPLATE_VERSIONS",
    "ProjectTemplate",
    "generate_ci",
    "get_template",
    "materialise",
    "validate_paths",
]
