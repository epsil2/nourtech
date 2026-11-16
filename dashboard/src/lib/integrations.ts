import { api } from "./api";

export type BoardIssue = {
  key: string;
  summary: string;
  type: string | null;
  status: string | null;
  assignee: string | null;
  priority: string | null;
  labels: string[];
  parent: string | null;
  sprints: { id: string; name: string; state: string }[];
  created: string;
  updated: string;
  url: string | null;
};

export type BoardFacetSprint = { id: string; name: string; state: string; issues: number };
export type BoardFacetFeature = {
  key: string;
  summary: string;
  type: string | null;
  status: string | null;
  issues: number;
};
export type BoardFacetStory = {
  key: string;
  summary: string;
  type: string | null;
  status: string | null;
  parent: string | null;
};

export type BacklogFacets = {
  sprints: BoardFacetSprint[];
  features: BoardFacetFeature[];
  stories: BoardFacetStory[];
  backlog_only: number;
};

export type BacklogFilters = {
  sprint?: string;
  feature?: string;
  story?: string;
  q?: string;
};

export type Backlog = {
  project: string;
  items: BoardIssue[];
  count: number;
  total: number;
  truncated: boolean;
  by_status: Record<string, number>;
  by_type: Record<string, number>;
  facets: BacklogFacets;
  filters: BacklogFilters & { active: number };
};

export type JiraProject = {
  key: string;
  name: string;
  style: string | null;
  board: number | null;
};

export type Sprint = {
  id: number | string;
  name: string;
  state: string;
  start?: string;
  end?: string;
  /** tickets of the current selection in this sprint */
  issues: number;
  /** tickets in this sprint before any filter */
  all_issues?: number;
  done?: number;
};

export type SprintsPayload = {
  board: number;
  project: string;
  items: Sprint[];
  count: number;
  active: Sprint | null;
  total: number;
  matching: number;
  selected: number;
  unscheduled: number;
  tickets: BoardIssue[];
  facets: BacklogFacets;
  filters: BacklogFilters & { active: number };
};

export type Repo = {
  full_name: string;
  private: boolean;
  default_branch: string;
  html_url: string;
  pushed_at: string;
  open_issues: number;
  permissions: Record<string, boolean>;
};

export type Pull = {
  number: number;
  title: string;
  state: string;
  draft: boolean;
  merged: boolean;
  author: string | null;
  branch: string | null;
  created_at: string;
  html_url: string;
};

export type Commit = { sha: string; message: string; author: string | null; date: string };

export type Run = {
  name: string;
  status: string;
  conclusion: string | null;
  branch: string | null;
  event: string | null;
  created_at: string;
  html_url: string;
};

export type GhRelease = {
  tag: string;
  name: string;
  draft: boolean;
  prerelease: boolean;
  published_at: string | null;
  html_url: string;
};

export type GitHubOverview = {
  repo: Repo;
  pulls: Pull[];
  commits: Commit[];
  actions: Run[];
  releases: GhRelease[];
};

export type Provider = {
  id: string;
  label: string;
  category: string;
  env: string[];
  what: string;
  read: boolean;
  write: boolean;
  configured: boolean;
  missing: string[];
  write_enabled?: boolean;
  site?: string | null;
  repo?: string | null;
};

export type Health = {
  host: {
    cpu_count: number;
    load_1m: number | null;
    load_5m: number | null;
    load_15m: number | null;
    memory: { total_kb: number; available_kb: number; used_percent: number | null; cgroup_limit_bytes: number | null };
    disk: { path: string; total_bytes: number; used_bytes: number; free_bytes: number; used_percent: number | null };
    uptime_seconds: number | null;
  };
  factory: {
    agents: number;
    specifications: number;
    specifications_approved: number;
    runs_by_status: Record<string, number>;
    audit_events: number;
  };
  gates: { human_approval_required: boolean; jira_write_enabled: boolean; deployment_requires_go_no_go: boolean };
  env: string;
};

export type AuditEvent = {
  id?: string;
  action: string;
  actor: string;
  entity_type?: string;
  entity_id?: string;
  created_at?: string;
  payload?: Record<string, unknown>;
};

export const board = {
  projects: () => api.get<{ items: JiraProject[]; count: number }>("/api/v1/integrations/jira/board/projects"),
  backlog: (project = "SCRUM", filters: BacklogFilters = {}, limit = 300, refresh = false) => {
    const params = new URLSearchParams({ project, limit: String(limit) });
    if (filters.sprint) params.set("sprint", filters.sprint);
    if (filters.feature) params.set("feature", filters.feature);
    if (filters.story) params.set("story", filters.story);
    if (filters.q) params.set("q", filters.q);
    if (refresh) params.set("refresh", "true");
    return api.get<Backlog>(`/api/v1/integrations/jira/board/backlog?${params.toString()}`);
  },
  sprints: (project = "SCRUM", filters: BacklogFilters = {}, refresh = false) => {
    const params = new URLSearchParams({ project });
    if (filters.sprint) params.set("sprint", filters.sprint);
    if (filters.feature) params.set("feature", filters.feature);
    if (filters.story) params.set("story", filters.story);
    if (filters.q) params.set("q", filters.q);
    if (refresh) params.set("refresh", "true");
    return api.get<SprintsPayload>(`/api/v1/integrations/jira/board/sprints?${params.toString()}`);
  },
};

export const gh = {
  status: () => api.get<{ configured: boolean; repo: string | null; mode: string; token_present: boolean }>(
    "/api/v1/integrations/github/status",
  ),
  overview: () => api.get<GitHubOverview>("/api/v1/integrations/github/overview"),
  actions: () => api.get<{ items: Run[]; count: number }>("/api/v1/integrations/github/actions?limit=10"),
  releases: () => api.get<{ items: GhRelease[]; count: number }>("/api/v1/integrations/github/releases?limit=10"),
};

export const system = {
  snapshot: () => api.get<Health>("/api/v1/system"),
  events: (limit = 20) => api.get<{ items: AuditEvent[]; count: number }>(`/api/v1/system/events?limit=${limit}`),
};

export const integrations = {
  list: () => api.get<{
    items: Provider[];
    count: number;
    configured: number;
    suggestions: { label: string; why: string }[];
    note: string;
  }>("/api/v1/integrations"),
};

export function bytes(n?: number | null): string {
  if (n === null || n === undefined) return "n/a";
  const gb = n / 1024 ** 3;
  return gb >= 1 ? `${gb.toFixed(1)} Go` : `${Math.round(n / 1024 ** 2)} Mo`;
}

export function day(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso.slice(0, 10) : d.toISOString().slice(0, 10);
}
