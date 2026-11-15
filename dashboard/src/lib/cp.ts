import { api } from "./api";

/* ---------- shapes served by the control-plane API (phase 2) ---------- */

export type ProjectCounts = { proposals: number; executions: number; issues: number };

export type Project = {
  id: string;
  key: string;
  name: string;
  business_request?: string;
  objectives?: string[];
  kpis?: string[];
  stakeholders?: string[];
  jira_project?: string;
  github_repo?: string;
  status?: string;
  stage?: string;
  created_at?: string;
  counts?: ProjectCounts;
};

export type ProposalItem = {
  key: string;
  type: string;
  summary: string;
  description?: string;
  labels?: string[];
  parent?: string | null;
  acceptance?: string[];
};

export type Proposal = {
  id: string;
  project_id?: string;
  title: string;
  status: string;
  jira_project?: string;
  items: ProposalItem[];
  summary?: { epics: number; stories: number; subtasks: number };
  approved_by?: string;
  approved_at?: string;
  result?: { created: number; existing: number; failed: number; failures: { item_key: string; error: string }[] };
  created_at?: string;
};

export type JiraIssue = {
  id: string;
  item_key: string;
  key: string;
  type: string;
  summary: string;
  status: string;
  url?: string;
};

export type Execution = {
  id: string;
  project_id: string;
  project_key?: string;
  objective: string;
  status: string;
  kanban_status?: string;
  task_id?: string | null;
  board?: string;
  jira_key?: string | null;
  agent_id?: string | null;
  runs?: number;
  error?: string | null;
  last_synced_at?: string;
  created_at?: string;
};

export type ExecutionEvent = {
  id: string;
  kind: string;
  message: string;
  created_at?: string;
};

export type Deployment = {
  id: string;
  project_key?: string;
  environment: string;
  version: string;
  commit_sha?: string;
  status: string;
  requested_by?: string;
  decided_by?: string;
  evidence?: string;
  note?: string;
  created_at?: string;
};

export type Job = {
  id: string;
  status: string;
  result?: { created?: unknown[]; existing?: unknown[]; failures?: { item_key: string; error: string }[] } | null;
  error?: string | null;
};

export type CpMetrics = {
  executions: { total: number; by_status: Record<string, number> };
  success_rate: number | null;
  mean_duration_seconds: number | null;
  issues: { total: number; by_status: Record<string, number> };
  deployments: { total: number; by_environment: Record<string, number> };
  proposals: Record<string, number>;
  cost: null;
  cost_note: string;
  generated_at: string;
};

export type EngineStatus = { available: boolean; binary: string | null; engine: string };

/* ---------- calls ---------- */

export const cp = {
  projects: () => api.get<{ projects: Project[]; total: number }>("/api/v1/projects"),
  createProject: (body: Partial<Project>) => api.post<Project>("/api/v1/projects", body),
  project: (id: string) =>
    api.get<{
      project: Project;
      proposals: Proposal[];
      executions: Execution[];
      issues: JiraIssue[];
      approvals: Record<string, unknown>[];
      deployments: Deployment[];
    }>(`/api/v1/projects/${id}`),
  propose: (projectId: string, specId?: string) =>
    api.post<Proposal>(`/api/v1/projects/${projectId}/proposals`, specId ? { spec_id: specId } : {}),
  proposal: (id: string) => api.get<Proposal & { issues: JiraIssue[] }>(`/api/v1/proposals/${id}`),
  approveProposal: (id: string, actor: string) =>
    api.post<{ proposal: Proposal }>(`/api/v1/proposals/${id}/approve`, { actor }),
  materialize: (id: string) =>
    api.post<{ job: Job; poll: string }>(`/api/v1/proposals/${id}/materialize`, {}),
  job: (path: string) => api.get<Job>(path),
  syncJira: (projectId: string) =>
    api.post<{ checked: number; updated: number }>(`/api/v1/projects/${projectId}/jira/sync`, {}),

  engine: () => api.get<EngineStatus>("/api/v1/executions/engine"),
  executions: (projectId?: string) =>
    api.get<{ executions: Execution[]; total: number }>(
      `/api/v1/executions${projectId ? `?project_id=${projectId}` : ""}`,
    ),
  execution: (id: string) =>
    api.get<{ execution: Execution; events: ExecutionEvent[] }>(`/api/v1/executions/${id}`),
  run: (body: {
    project_id: string;
    objective: string;
    dispatch?: boolean;
    jira_key?: string;
    max_runtime?: string;
  }) => api.post<Execution>("/api/v1/executions", body),
  syncExecution: (id: string) => api.post<Execution>(`/api/v1/executions/${id}/sync`, {}),
  cancelExecution: (id: string, reason: string) =>
    api.post<Execution>(`/api/v1/executions/${id}/cancel`, { reason }),

  environments: () => api.get<{ environments: { id: string; name: string; gate: boolean }[] }>("/api/v1/environments"),
  deployments: (projectId?: string) =>
    api.get<{ deployments: Deployment[]; total: number }>(
      `/api/v1/deployments${projectId ? `?project_id=${projectId}` : ""}`,
    ),
  requestDeployment: (body: { project_id: string; environment: string; version: string; commit_sha?: string }) =>
    api.post<Deployment>("/api/v1/deployments", body),
  confirmDeployment: (id: string, body: { outcome: string; actor: string; evidence?: string; go?: boolean }) =>
    api.post<Deployment>(`/api/v1/deployments/${id}/confirm`, body),
  rollback: (id: string, reason: string) => api.post<Deployment>(`/api/v1/deployments/${id}/rollback`, { reason }),
  metrics: () => api.get<CpMetrics>("/api/v1/cp/metrics"),
};
