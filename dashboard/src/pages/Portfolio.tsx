import { useMemo, useState } from "react";
import { cp } from "../lib/cp";
import type { Project } from "../lib/cp";
import { ErrorLine, Loading, Tag, ago } from "../components/ui";
import { useAsync } from "../lib/useAsync";

type Row = {
  id: string;
  key: string;
  name: string;
  jira_project: string | null;
  status: string;
  source: "camilia" | "jira";
  board: number | null;
  project?: Project;
  updated_at?: string;
};

const STATUSES = ["active", "draft", "paused", "closed"] as const;

const TONE: Record<string, "ok" | "warn" | "bad" | "info" | "mut"> = {
  active: "ok",
  draft: "info",
  paused: "warn",
  closed: "mut",
  archived: "mut",
};

const BLANK_EDIT = {
  name: "",
  business_request: "",
  status: "active",
  scope: "",
  constraints: "",
  risks: "",
  objectives: "",
  kpis: "",
  stakeholders: "",
};

function toList(value: string): string[] {
  return value
    .split(/\n|,/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function EditModal({
  row,
  onClose,
  onSaved,
}: {
  row: Row;
  onClose: () => void;
  onSaved: () => void;
}) {
  const project = row.project;
  const [form, setForm] = useState(() => ({
    ...BLANK_EDIT,
    name: project?.name ?? row.name,
    business_request: project?.business_request ?? "",
    status: project?.status ?? row.status ?? "active",
    scope: project?.scope ?? "",
    constraints: project?.constraints ?? "",
    risks: project?.risks ?? "",
    objectives: (project?.objectives ?? []).join("\n"),
    kpis: (project?.kpis ?? []).join("\n"),
    stakeholders: (project?.stakeholders ?? []).join("\n"),
  }));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const set = (key: keyof typeof form) => (event: { target: { value: string } }) =>
    setForm((prev) => ({ ...prev, [key]: event.target.value }));

  const submit = async () => {
    if (!form.name.trim()) {
      setError("Le nom du projet est obligatoire.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      // A Jira project without a Camilia row has no id we can patch: create the
      // row first (same shape as the deployment import), then patch it.
      let id = row.id;
      if (row.source === "jira") {
        const created = await cp.createProject({
          key: row.key,
          name: form.name.trim(),
          jira_project: row.jira_project ?? row.key,
          status: form.status,
          business_request: form.business_request,
          objectives: toList(form.objectives),
          kpis: toList(form.kpis),
          stakeholders: toList(form.stakeholders),
          scope: form.scope,
          constraints: form.constraints,
          risks: form.risks,
        });
        id = created.id;
      } else {
        await cp.updateProject(id, {
          name: form.name.trim(),
          business_request: form.business_request,
          status: form.status,
          scope: form.scope,
          constraints: form.constraints,
          risks: form.risks,
          objectives: toList(form.objectives),
          kpis: toList(form.kpis),
          stakeholders: toList(form.stakeholders),
        });
      }
      onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="overlay" role="dialog" aria-modal="true" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal__head">
          <h2>
            <span className="ico">✎</span> Éditer · {row.key}
          </h2>
          <button className="iconbtn" type="button" onClick={onClose} aria-label="Fermer">
            ✕
          </button>
        </div>
        <div className="modal__body">
          {row.source === "jira" && (
            <p className="foot-note" style={{ marginBottom: 8 }}>
              Ce projet existe dans Jira mais pas encore dans Camilia : l'enregistrement le crée dans le
              portefeuille, puis applique vos modifications.
            </p>
          )}
          <label className="field">
            <span>Nom du projet</span>
            <input value={form.name} onChange={set("name")} placeholder="Refonte du portail client" />
          </label>
          <label className="field">
            <span>Demande métier</span>
            <textarea rows={3} value={form.business_request} onChange={set("business_request")} />
          </label>
          <div className="grid-2">
            <label className="field">
              <span>Statut</span>
              <select className="inp" value={form.status} onChange={set("status")}>
                {STATUSES.map((status) => (
                  <option key={status} value={status}>
                    {status}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Périmètre</span>
              <input value={form.scope} onChange={set("scope")} placeholder="Parcours de commande B2B" />
            </label>
          </div>
          <label className="field">
            <span>Objectifs (un par ligne)</span>
            <textarea rows={3} value={form.objectives} onChange={set("objectives")} />
          </label>
          <label className="field">
            <span>Indicateurs de succès (un par ligne)</span>
            <textarea rows={2} value={form.kpis} onChange={set("kpis")} />
          </label>
          <label className="field">
            <span>Parties prenantes (un par ligne)</span>
            <textarea rows={2} value={form.stakeholders} onChange={set("stakeholders")} />
          </label>
          <div className="grid-2">
            <label className="field">
              <span>Contraintes</span>
              <textarea rows={2} value={form.constraints} onChange={set("constraints")} />
            </label>
            <label className="field">
              <span>Risques</span>
              <textarea rows={2} value={form.risks} onChange={set("risks")} />
            </label>
          </div>
          {error && <ErrorLine>{error}</ErrorLine>}
        </div>
        <div className="modal__foot">
          <button className="btn ghost" type="button" onClick={onClose} disabled={busy}>
            Annuler
          </button>
          <button className="btn" type="button" onClick={submit} disabled={busy}>
            <span className="pict">💾</span> {busy ? "Enregistrement…" : "Enregistrer"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function Portfolio({ onOpenFactory }: { onOpenFactory: () => void }) {
  const [camilia, refreshCamilia] = useAsync(() => cp.projects());
  const [merged, refreshMerged] = useAsync(() => cp.deploymentProjects());
  const [needle, setNeedle] = useState("");
  const [status, setStatus] = useState("");
  const [editing, setEditing] = useState<Row | null>(null);

  const rows = useMemo<Row[]>(() => {
    const byKey = new Map<string, Row>();
    if (merged.state === "ok") {
      for (const item of merged.data.projects) {
        byKey.set(item.key.toUpperCase(), {
          id: item.id,
          key: item.key,
          name: item.name,
          jira_project: item.jira_project,
          status: "active",
          source: item.source,
          board: item.board,
        });
      }
    }
    if (camilia.state === "ok") {
      for (const project of camilia.data.projects) {
        const key = (project.key || project.id).toUpperCase();
        const existing = byKey.get(key);
        byKey.set(key, {
          id: project.id,
          key: project.key || project.id,
          name: project.name,
          jira_project: project.jira_project ?? null,
          status: project.status ?? "active",
          source: "camilia",
          board: existing?.board ?? null,
          project,
          updated_at: project.updated_at ?? project.created_at,
        });
      }
    }
    return [...byKey.values()].sort((a, b) => a.key.localeCompare(b.key));
  }, [camilia, merged]);

  const visible = rows.filter((row) => {
    if (status && row.status !== status) return false;
    if (!needle.trim()) return true;
    const haystack = `${row.key} ${row.name} ${row.jira_project ?? ""}`.toLowerCase();
    return haystack.includes(needle.trim().toLowerCase());
  });

  const stats = {
    total: rows.length,
    camilia: rows.filter((r) => r.source === "camilia").length,
    jira: rows.filter((r) => r.jira_project).length,
    active: rows.filter((r) => r.status === "active").length,
  };

  const error =
    camilia.state === "error" && merged.state === "error"
      ? camilia.error
      : null;

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Portefeuille projets</h1>
          <p className="sub">
            La vue business de vos projets : demande métier, objectifs, indicateurs et rattachement Jira.
            Chaque ligne s'édite sans quitter la page ; les projets qui n'existent que dans Jira sont
            importés au premier enregistrement.
          </p>
        </div>
        <div className="head-right">
          <button className="btn ghost" type="button" onClick={() => (refreshCamilia(), refreshMerged())}>
            <span className="pict">⟳</span> Actualiser
          </button>
          <button className="btn" type="button" onClick={onOpenFactory}>
            <span className="pict">＋</span> Nouveau projet
          </button>
        </div>
      </div>

      <div className="cards">
        <div className="card">
          <div className="lab">
            <span className="ico">▦</span> Projets
          </div>
          <div className="val">{stats.total}</div>
          <div className="delta flat">portefeuille consolidé</div>
        </div>
        <div className="card">
          <div className="lab">
            <span className="ico">◉</span> Actifs
          </div>
          <div className="val">{stats.active}</div>
          <div className="delta">en cours de livraison</div>
        </div>
        <div className="card">
          <div className="lab">
            <span className="ico">◈</span> Liés à Jira
          </div>
          <div className="val">{stats.jira}</div>
          <div className="delta flat">backlog synchronisé</div>
        </div>
        <div className="card">
          <div className="lab">
            <span className="ico">◔</span> Dans Camilia
          </div>
          <div className="val">{stats.camilia}</div>
          <div className="delta flat">éditables telles quelles</div>
        </div>
      </div>

      <div className="filters">
        <label className="field">
          <span>Recherche</span>
          <input
            className="inp"
            value={needle}
            onChange={(e) => setNeedle(e.target.value)}
            placeholder="clé, nom ou projet Jira"
          />
        </label>
        <label className="field">
          <span>Statut</span>
          <select className="inp" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Tous les statuts</option>
            {STATUSES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <div className="field filter-reset">
          <span>&nbsp;</span>
          <button
            className="btn ghost"
            type="button"
            onClick={() => {
              setNeedle("");
              setStatus("");
            }}
          >
            <span className="pict">✕</span> Réinitialiser
          </button>
        </div>
      </div>

      <section className="panel">
        <div className="panel-head">
          <h2>▦ Projets · {visible.length}</h2>
          <span className="chip">édition en ligne</span>
        </div>
        {error && <ErrorLine>{error}</ErrorLine>}
        {(camilia.state === "loading" || merged.state === "loading") && rows.length === 0 && <Loading rows={4} />}
        {rows.length === 0 && camilia.state === "ok" && merged.state === "ok" && (
          <div className="empty">
            Aucun projet pour l'instant. Créez-en un depuis Project Factory, ou rattachez un projet Jira.
          </div>
        )}
        {visible.length > 0 && (
          <table className="tbl">
            <thead>
              <tr>
                <th>Projet</th>
                <th>Demande métier</th>
                <th>Statut</th>
                <th>Jira</th>
                <th>Source</th>
                <th>Mis à jour</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {visible.map((row) => (
                <tr key={row.key}>
                  <td>
                    <b>{row.key}</b>
                    <div className="mono">{row.name}</div>
                  </td>
                  <td className="col-summary">{row.project?.business_request ?? "—"}</td>
                  <td>
                    <Tag tone={TONE[row.status] ?? "mut"}>{row.status}</Tag>
                  </td>
                  <td className="mono">{row.jira_project ?? "—"}</td>
                  <td>
                    <span className="mono">{row.source === "jira" ? "Jira" : "Camilia"}</span>
                  </td>
                  <td className="mono">{row.updated_at ? ago(row.updated_at) : "—"}</td>
                  <td>
                    <button className="btn ghost sm" type="button" onClick={() => setEditing(row)}>
                      <span className="pict">✎</span> Éditer
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {rows.length > 0 && visible.length === 0 && (
          <div className="empty">
            Aucun projet ne correspond à ce filtre.{" "}
            <button
              className="link"
              type="button"
              onClick={() => {
                setNeedle("");
                setStatus("");
              }}
            >
              Réinitialiser
            </button>
          </div>
        )}
      </section>

      {editing && (
        <EditModal
          row={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            refreshCamilia();
            refreshMerged();
          }}
        />
      )}
    </div>
  );
}
