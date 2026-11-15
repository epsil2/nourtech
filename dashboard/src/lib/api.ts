export type Json = Record<string, unknown>;

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    credentials: "same-origin",
    ...init,
    headers: { accept: "application/json", ...(init?.headers ?? {}) },
  });
  if (res.status === 401) {
    // The session is gone or was never opened: let the shell show the login screen.
    window.dispatchEvent(new CustomEvent("camilia:unauthorised"));
  }
  const text = await res.text();
  let payload: unknown = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = { detail: text.slice(0, 300) };
  }
  if (!res.ok) {
    const detail = (payload as Json | null)?.detail;
    throw new ApiError(res.status, typeof detail === "string" ? detail : `HTTP ${res.status}`);
  }
  return payload as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body: unknown) =>
    request<T>(path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }),
};

/* The browser no longer asserts who it is: the server derives the actor from the session
   cookie. These helpers only label what the server already told us, for display. */
export function actor(): string {
  return localStorage.getItem("camilia.actor") ?? "—";
}

export function setActor(value: string) {
  localStorage.setItem("camilia.actor", value);
}

/* ---------- shapes actually served by the AI Factory API ---------- */

export type WorkflowNode = { name: string; approval: boolean; label: string };
export type WorkflowDefinition = { name: string; nodes: WorkflowNode[] };

export type Workflow = {
  id: string;
  definition: string;
  status: string;
  current_step: string;
  created_at?: string;
  updated_at?: string;
  state?: Json;
};

export type Evaluation = {
  id: number;
  subject: string;
  dataset: string;
  score: number;
  threshold: number;
  passed: boolean;
  review_outcome?: string | null;
  created_at?: string;
};

export type Plan = {
  id: string;
  issue_key: string;
  status: string;
  payload?: Json;
  created_at?: string;
};

export type AuditRow = {
  id?: number;
  actor?: string;
  action: string;
  entity_type?: string;
  entity_id?: string;
  payload?: Json;
  at?: string;
};

export type Release = {
  id: string;
  version?: string;
  target?: string;
  status: string;
  commit_sha?: string;
  created_at?: string;
};

export type Metrics = {
  uptime_seconds: number;
  runs_total: number;
  runs_by_status: Record<string, number>;
  audit_events_total: number;
  store: string;
};

export type Resources = {
  uptime_s: number;
  cpu_count: number;
  memory_mib: { MemTotal: number; MemAvailable: number; SwapTotal: number; SwapFree: number };
  disk: { path: string; total_mib: number; used_mib: number; used_pct: number };
  runs: Record<string, number>;
  audit_events: number;
  agents: number;
  releases: number;
  artifacts: number;
};

export type JiraStatus = {
  configured: boolean;
  site?: string;
  write_enabled?: boolean;
  write_projects?: string[];
  read_only?: boolean;
};

export type Agent = {
  id: string;
  name: string;
  description?: string;
  status?: string;
  created_at?: string;
};

export const listOf = <T>(payload: { items?: T[]; count?: number } | null | undefined): T[] =>
  payload?.items ?? [];

export type Async<T> =
  | { state: "loading" }
  | { state: "ok"; data: T }
  | { state: "error"; error: string };
