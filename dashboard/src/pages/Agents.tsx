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
  const [edit, setEdit] = useState<Agent | null>(null);
  const [form, setForm] = useState({ name: "", description: "" });
  const [saving, setSaving] = useState(false);
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

  const startEdit = (agent: Agent) => {
    setEdit(agent);
    setForm({ name: agent.name, description: agent.description ?? "" });
    setError("");
  };

  const save = async () => {
    if (!edit) return;
    setSaving(true);
    setError("");
    try {
      const body: { name?: string; description?: string } = {};
      if (form.name.trim() && form.name.trim() !== edit.name) body.name = form.name.trim();
      if (form.description !== (edit.description ?? "")) body.description = form.description;
      if (Object.keys(body).length === 0) {
        setEdit(null);
        return;
      }
      await api.patch(`/api/v1/agents/${edit.id}`, body);
      setEdit(null);
      load();
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setSaving(false);
    }
  };

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

      {edit && (
        <Panel title={`Modifier ${edit.name}`}>
          <div className="field">
            <label htmlFor="agent-name">Nom</label>
            <input
              id="agent-name"
              className="inp"
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
            />
          </div>
          <div className="field">
            <label htmlFor="agent-desc">Description</label>
            <textarea
              id="agent-desc"
              className="inp"
              rows={3}
              value={form.description}
              onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
            />
          </div>
          <div className="row-actions">
            <button className="btn" onClick={save} disabled={saving} type="button">
              {saving ? "Enregistrement…" : "Enregistrer"}
            </button>
            <button className="btn ghost" onClick={() => setEdit(null)} disabled={saving} type="button">
              Annuler
            </button>
          </div>
          <p className="sub" style={{ marginTop: 6 }}>
            Le renommage est refusé si le nom existe déjà. Chaque modification est auditée avec votre nom
            d'acteur.
          </p>
        </Panel>
      )}

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
              <span className="row-actions">
                <button className="btn ghost sm" onClick={() => startEdit(a)} type="button">
                  Modifier
                </button>
              </span>
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
