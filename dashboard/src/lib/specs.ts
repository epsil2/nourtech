import { api } from "./api";

export type Acceptance = { given: string; when: string; then: string };

export type Story = {
  key: string;
  skill: string;
  title: string;
  as_a: string;
  i_want: string;
  so_that: string;
  acceptance: Acceptance[];
  tasks: string[];
  depends_on: string[];
  definition_of_done: string[];
};

export type Spec = {
  spec_version: string;
  status: string;
  name: string;
  slug: string;
  objective: string;
  problem: string;
  outcomes: string[];
  constraints: string[];
  skills: string[];
  tools: { id: string; label: string }[];
  permissions: { id: string; label: string }[];
  kpis: { name: string; target: string; measure: string }[];
  llm: { provider: string; model: string; note: string };
  architecture: { style: string; stack: string[]; components: { name: string; role: string }[] };
  workflow: { steps: { id: string; actor: string; action: string; gate: boolean }[]; human_gates: string[] };
  backlog: { epic: { title: string; summary: string }; stories: Story[] };
  test_plan: { scenario: string; given: string; when: string; then: string }[];
  roadmap: { sprint: number; goal: string; deliverables: string[] }[];
  risks: { risk: string; impact: string; mitigation: string }[];
  generated_by: string;
};

export type SpecRow = {
  id: string;
  slug: string;
  status: string;
  payload: Spec;
  agent_id: string | null;
  approved_by: string | null;
  approved_at: string | null;
  created_at: string;
};

export type Intake = {
  name: string;
  objective: string;
  problem: string;
  outcomes: string[];
  constraints: string[];
  skills: string[];
  tools: string[];
  permissions: string[];
  kpis: string[];
  llm_provider?: string | null;
  llm_model?: string | null;
};

export type Catalog = {
  skills: { id: string; label: string; story: string }[];
  tools: { id: string; label: string }[];
  permissions: { id: string; label: string }[];
  human_gates: { id: string; label: string; button: string }[];
};

export const specs = {
  catalog: () => api.get<Catalog>("/api/v1/specifications/catalog"),
  draft: (intake: Intake) => api.post<Spec>("/api/v1/specifications/draft", intake),
  create: (intake: Intake) => api.post<SpecRow>("/api/v1/specifications", intake),
  approve: (id: string) => api.post<SpecRow>(`/api/v1/specifications/${id}/approve`, {}),
  list: () => api.get<{ items: SpecRow[]; count: number }>("/api/v1/specifications"),
};
