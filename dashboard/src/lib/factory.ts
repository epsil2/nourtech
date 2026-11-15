import { api } from "./api";

/* ---------- shapes served by the Project Factory API ---------- */

export type TemplateStack = Record<string, string>;

export type FactoryTemplate = {
  key: string;
  version: string;
  name: string;
  description: string;
  stack: TemplateStack | string | null;
};

export type Catalogue = {
  project_types: string[];
  visibilities: string[];
  templates: FactoryTemplate[];
  jira: { ready: boolean; site: string | null; detail: string };
  github: { ready: boolean; owner: string | null; detail: string };
  states: string[];
  steps: { key: string; label: string }[];
};

export type FactoryProjectSummary = {
  id: string;
  key: string;
  name: string;
  project_type: string | null;
  template: string | null;
  state: string;
  updated_at: string | null;
  created_by: string | null;
};

export type FactoryProjectList = {
  items: FactoryProjectSummary[];
  counts: Record<string, number>;
  total: number;
};

export type FactoryProject = {
  id: string;
  key: string;
  name: string;
  description?: string | null;
  business_objective?: string | null;
  project_type?: string | null;
  business_domain?: string | null;
  owner?: string | null;
  visibility?: string | null;
  template?: string | null;
  requirements?: string | null;
  created_by?: string | null;
  created_at?: string | null;
  jira_mode?: string | null;
  jira_existing_key?: string | null;
};

export type FactorySpec = {
  version: number;
  status: string;
  content: Record<string, unknown>;
};

export type Provisioning = {
  state?: string | null;
  last_error?: string | null;
  failed_step?: string | null;
};

export type FactoryJira = {
  jira_key?: string | null;
  url?: string | null;
  created?: boolean | null;
};

export type FactoryIssue = {
  key: string;
  issue_type: string | null;
  summary: string | null;
  url: string | null;
  epic_key: string | null;
};

export type FactoryRepo = {
  full_name?: string | null;
  url?: string | null;
  default_branch?: string | null;
  private?: boolean | null;
  template_commit?: string | null;
};

export type FactoryWorkspace = {
  board?: string | null;
  workspace_path?: string | null;
};

export type FactoryApproval = {
  actor: string;
  decision: string;
  notes?: string | null;
  created_at?: string | null;
};

export type FactoryExecution = {
  task_id?: string | null;
  board?: string | null;
  issue_key?: string | null;
  status: string;
  idempotency_key?: string | null;
  launched_at?: string | null;
};

export type FactoryOperation = {
  step: string;
  status: string;
  attempt?: number | null;
  started_at?: string | null;
  completed_at?: string | null;
  external_id?: string | null;
  error?: string | null;
  idempotency_key?: string | null;
};

export type FactoryCiRun = {
  name?: string | null;
  status?: string | null;
  conclusion?: string | null;
  url?: string | null;
  head_sha?: string | null;
};

export type ProjectDetail = {
  project: FactoryProject;
  provisioning?: Provisioning | null;
  state: string;
  spec?: FactorySpec | null;
  jira?: FactoryJira | null;
  jira_issues?: FactoryIssue[] | null;
  repository?: FactoryRepo | null;
  workspace?: FactoryWorkspace | null;
  approvals?: FactoryApproval[] | null;
  executions?: FactoryExecution[] | null;
  operations?: FactoryOperation[] | null;
  ci_runs?: FactoryCiRun[] | null;
  next_step?: string | null;
};

export type FactoryAuditItem = {
  action: string;
  actor?: string | null;
  entity_type?: string | null;
  created_at?: string | null;
  at?: string | null;
  payload?: Record<string, unknown> | null;
};

export type CreateProjectBody = {
  key: string;
  name: string;
  description: string;
  business_objective: string;
  project_type: string;
  business_domain: string;
  owner: string;
  visibility: string;
  template: string;
  jira_mode: "new" | "existing";
  jira_existing_key: string;
  requirements: string;
};

export type ProjectPatchBody = Partial<{
  name: string;
  description: string;
  business_objective: string;
  project_type: string;
  business_domain: string;
  owner: string;
  visibility: string;
  template: string;
  requirements: string;
}>;

export type SpecWriteBody = {
  requirements?: string;
  content?: Record<string, unknown>;
};

export type FactoryJob = { id: string; status: string };

/* ---------- calls ---------- */

const BASE = "/api/v1/factory";

export const factory = {
  catalogue: () => api.get<Catalogue>(`${BASE}/catalogue`),

  projects: () => api.get<FactoryProjectList>(`${BASE}/projects`),

  create: (body: CreateProjectBody) => api.post<{ project: ProjectDetail }>(`${BASE}/projects`, body),

  project: (id: string) => api.get<ProjectDetail>(`${BASE}/projects/${id}`),

  patch: (id: string, body: ProjectPatchBody) =>
    api.patch<{ project: ProjectDetail }>(`${BASE}/projects/${id}`, body),

  writeSpec: (id: string, body: SpecWriteBody) =>
    api.post<{ spec: FactorySpec; project: ProjectDetail }>(`${BASE}/projects/${id}/spec`, body),

  submitSpec: (id: string) => api.post<{ project: ProjectDetail }>(`${BASE}/projects/${id}/spec/submit`, {}),

  approve: (id: string, notes: string) =>
    api.post<{ project: ProjectDetail; approval: FactoryApproval }>(`${BASE}/projects/${id}/approve`, { notes }),

  provision: (id: string) =>
    api.post<{ job: FactoryJob; project: ProjectDetail }>(`${BASE}/projects/${id}/provision`, {}),

  retry: (id: string) => api.post<{ job: FactoryJob; step: string }>(`${BASE}/projects/${id}/retry`, {}),

  backlog: (id: string) => api.post<{ job: FactoryJob }>(`${BASE}/projects/${id}/backlog`, {}),

  development: (id: string, body: { issue_key: string; runtime_limit: string; retry_limit: number }) =>
    api.post<{ execution: FactoryExecution; project: ProjectDetail }>(`${BASE}/projects/${id}/development`, body),

  audit: (id: string) => api.get<{ items: FactoryAuditItem[] }>(`${BASE}/projects/${id}/audit`),
};

/* ---------- state helpers shared by the pages ---------- */

const PROVISIONING = ["CREATING_", "APPLYING_", "CONFIGURING_", "REGISTERING_", "VALIDATING"];

const SETTLED = [
  "READY",
  "FAILED",
  "BLOCKED",
  "CANCELLED",
  "REQUIRES_INTERVENTION",
  "DEVELOPMENT_RUNNING",
  "DEVELOPMENT_COMPLETED",
];

export function isProvisioning(state: string): boolean {
  return PROVISIONING.some((prefix) => state.includes(prefix));
}

export function isSettled(state: string): boolean {
  return SETTLED.includes(state);
}

export function isFailure(state: string): boolean {
  return ["FAILED", "REQUIRES_INTERVENTION", "BLOCKED"].includes(state);
}
