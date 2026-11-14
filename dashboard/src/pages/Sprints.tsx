import { useState } from "react";
import { Empty, ErrorLine, Kpi, Loading, Panel, Tag, statusTone } from "../components/ui";
import { useAsync } from "../lib/useAsync";
import { board } from "../lib/integrations";
import type { Sprint } from "../lib/integrations";

const SPRINT_TONE: Record<string, "ok" | "info" | "mut" | "warn"> = {
  active: "ok",
  future: "info",
  closed: "mut",
};

const SPRINT_LABEL: Record<string, string> = {
  active: "en cours",
  future: "à venir",
  closed: "clôturé",
};

export function Sprints() {
  const [data, refresh] = useAsync(() => board.sprints("SCRUM", 1));
  const [busy, setBusy] = useState(false);

  const update = () => {
    setBusy(true);
    refresh();
    setTimeout(() => setBusy(false), 1200);
  };

  if (data.state === "error") {
    return (
      <ErrorLine>
        {data.error} — la lecture des sprints utilise l'API Agile de Jira (board 1) ; vérifiez les droits du
        compte de service.
      </ErrorLine>
    );
  }

  const items: Sprint[] = data.state === "ok" ? data.data.items : [];
  const active = data.state === "ok" ? data.data.active : null;
  const totalIssues = items.reduce((n, s) => n + (s.issues ?? 0), 0);

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Sprints</h1>
          <p className="sub">
            Board <b>1</b>, projet <b>SCRUM</b>. Lecture seule : cette page ne démarre ni ne clôture un sprint.
            Le nombre de tickets par sprint est compté depuis le backlog, pas déclaré à la main.
          </p>
        </div>
        <div className="head-right">
          <button className="btn" onClick={update} disabled={busy || data.state === "loading"} type="button">
            {busy || data.state === "loading" ? "Mise à jour…" : "↻ Update"}
          </button>
        </div>
      </div>

      <div className="cards">
        <Kpi icon="◷" label="Sprints" value={data.state === "ok" ? String(items.length) : "…"} hint="sur le board 1" />
        <Kpi
          icon="▶"
          label="Sprint actif"
          value={active ? active.name.replace(/^Sprint\s*/i, "S") : "aucun"}
          hint={active ? `${active.issues} tickets` : "aucun sprint ouvert — à créer dans Jira"}
          tone={active ? "ok" : "warn"}
        />
        <Kpi icon="▤" label="Tickets planifiés" value={String(totalIssues)} hint="avec un sprint renseigné" />
      </div>

      <Panel
        title="Sprints du board"
        action={<Tag tone="mut">{items.length} au total</Tag>}
      >
        {data.state === "loading" ? (
          <Loading rows={4} />
        ) : items.length === 0 ? (
          <Empty>Aucun sprint sur ce board.</Empty>
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th>#</th>
                <th>Sprint</th>
                <th>État</th>
                <th>Début</th>
                <th>Fin</th>
                <th>Tickets</th>
              </tr>
            </thead>
            <tbody>
              {items.map((s) => (
                <tr key={String(s.id)}>
                  <td className="mono">{String(s.id)}</td>
                  <td>{s.name}</td>
                  <td>
                    <Tag tone={SPRINT_TONE[s.state] ?? statusTone(s.state)}>{SPRINT_LABEL[s.state] ?? s.state}</Tag>
                  </td>
                  <td className="mono">{s.start ?? "—"}</td>
                  <td className="mono">{s.end ?? "—"}</td>
                  <td className="mono">{s.issues ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      <Panel title="Ce que cette page ne fait pas">
        <div className="kv">
          <span>Démarrer ou clôturer un sprint</span>
          <b>à faire dans Jira — geste explicite, jamais automatique</b>
        </div>
        <div className="kv">
          <span>Modifier la portée d'un sprint</span>
          <b>lecture seule ici</b>
        </div>
        <div className="kv">
          <span>Créer des tickets</span>
          <b>après approbation du backlog (Sprint 3)</b>
        </div>
      </Panel>
    </div>
  );
}
