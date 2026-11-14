import { useEffect, useState } from "react";
import { Panel, Tag, Empty, Loading, ErrorLine, ago } from "../components/ui";
import { SpecReview, SpecStatus, Wizard } from "../components/Wizard";
import { api } from "../lib/api";
import type { Agent } from "../lib/api";
import { specs } from "../lib/specs";
import type { SpecRow } from "../lib/specs";

export function Agents() {
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [rows, setRows] = useState<SpecRow[] | null>(null);
  const [selected, setSelected] = useState<SpecRow | null>(null);
  const [wizard, setWizard] = useState(false);
  const [error, setError] = useState("");

  const load = () => {
    Promise.all([api.get<{ items: Agent[] }>("/api/v1/agents"), specs.list()])
      .then(([a, s]) => {
        setAgents(a.items);
        setRows(s.items);
      })
      .catch((e) => setError(String(e.message ?? e)));
  };

  useEffect(load, []);

  if (wizard) {
    return (
      <Wizard
        onClose={() => {
          setWizard(false);
          load();
        }}
        onApproved={() => setError("")}
      />
    );
  }

  if (selected) {
    return (
      <div className="stack">
        <div className="page-head">
          <div>
            <h1>{selected.payload.name}</h1>
            <p className="sub">
              <SpecStatus status={selected.status} /> · créé {ago(selected.created_at)}
              {selected.approved_by ? ` · approuvé par ${selected.approved_by}` : ""}
            </p>
          </div>
          <button className="btn ghost" onClick={() => setSelected(null)} type="button">
            Retour
          </button>
        </div>
        <SpecReview spec={selected.payload} />
      </div>
    );
  }

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Agents</h1>
          <p className="sub">
            Registre des agents et spécifications. Rien n'est créé dans Jira avant votre approbation.
          </p>
        </div>
        <button className="btn" onClick={() => setWizard(true)} type="button">
          + Create New Agent
        </button>
      </div>

      {error && <ErrorLine>{error}</ErrorLine>}

      <Panel title="Registre" action={<Tag tone="mut">{agents?.length ?? 0} agents</Tag>}>
        {agents === null ? (
          <Loading rows={2} />
        ) : agents.length === 0 ? (
          <Empty>
            Aucun agent enregistré. Un agent naît d'une spécification approuvée — cliquez sur « Create New Agent ».
          </Empty>
        ) : (
          agents.map((a) => (
            <div key={a.id} className="row-item">
              <b>{a.name}</b>
              <span>{a.description || "sans description"}</span>
              <Tag tone="ok">enregistré</Tag>
            </div>
          ))
        )}
      </Panel>

      <Panel title="Spécifications" action={<Tag tone="mut">{rows?.length ?? 0} documents</Tag>}>
        {rows === null ? (
          <Loading rows={2} />
        ) : rows.length === 0 ? (
          <Empty>Le wizard génère une spécification complète : architecture, workflow, backlog, KPI, risques.</Empty>
        ) : (
          rows.map((r) => (
            <button key={r.id} className="row-item clickable" onClick={() => setSelected(r)} type="button">
              <b>{r.payload.name || r.slug}</b>
              <span>{r.payload.objective}</span>
              <span className="mono">{r.payload.backlog.stories.length} stories</span>
              <SpecStatus status={r.status} />
            </button>
          ))
        )}
      </Panel>
    </div>
  );
}
