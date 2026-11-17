import { useMemo, useState } from "react";
import { Empty, ErrorLine, Kpi, Loading, Meter, Note, Panel, Tag, statusTone } from "../components/ui";
import { useAsync } from "../lib/useAsync";
import { board } from "../lib/integrations";
import type { BacklogFacets, Sprint } from "../lib/integrations";

const DEFAULT_PROJECT = "SCRUM";
const NO_SPRINT = "none";

const SPRINT_TONE: Record<string, "ok" | "info" | "mut"> = {
  active: "ok",
  future: "info",
  closed: "mut",
};

const SPRINT_LABEL: Record<string, string> = {
  active: "en cours",
  future: "à venir",
  closed: "clôturé",
};

function shorten(text: string, max = 52): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

export function Sprints() {
  const [project, setProject] = useState(() => localStorage.getItem("camilia.backlog.project") || DEFAULT_PROJECT);
  const [sprint, setSprint] = useState("");
  const [feature, setFeature] = useState("");
  const [story, setStory] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);

  const filters = useMemo(
    () => ({
      ...(sprint ? { sprint } : {}),
      ...(feature ? { feature } : {}),
      ...(story ? { story } : {}),
    }),
    [sprint, feature, story],
  );

  const [data] = useAsync(() => board.sprints(project, filters, refreshKey > 0), [
    project,
    sprint,
    feature,
    story,
    refreshKey,
  ]);
  const [projects] = useAsync(() => board.projects());

  const facets: BacklogFacets | null = data.state === "ok" ? data.data.facets : null;
  const items: Sprint[] = data.state === "ok" ? data.data.items : [];
  const tickets = data.state === "ok" ? data.data.tickets : [];

  const storyOptions = useMemo(() => {
    if (!facets) return [];
    return feature ? facets.stories.filter((s) => s.parent === feature) : facets.stories;
  }, [facets, feature]);

  const projectOptions = useMemo(() => {
    const rows = projects.state === "ok" ? projects.data.items : [];
    if (rows.length || projects.state === "loading") return rows;
    return [{ key: DEFAULT_PROJECT, name: "Ai Factory Software Team", style: null, board: null }];
  }, [projects]);

  const onProject = (value: string) => {
    setProject(value);
    localStorage.setItem("camilia.backlog.project", value);
    setSprint("");
    setFeature("");
    setStory("");
  };

  const reset = () => {
    setSprint("");
    setFeature("");
    setStory("");
  };

  const active = data.state === "ok" ? data.data.active : null;
  const selected = sprint && sprint !== NO_SPRINT ? items.find((s) => String(s.id) === sprint) : null;
  const scopeLabel = selected
    ? shorten(selected.name, 32)
    : sprint === NO_SPRINT
      ? "backlog (sans sprint)"
      : null;

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Sprints</h1>
          <p className="sub">
            Planification lue en direct depuis Jira. Filtrez par projet, sprint, fonctionnalité ou user story ;
            choisissez <b>« Sans sprint »</b> pour voir le backlog non planifié. Cette page ne démarre ni ne
            clôture un sprint.
          </p>
        </div>
        <div className="head-right">
          <button className="btn ghost" type="button" onClick={reset} disabled={!sprint && !feature && !story}>
            <span className="pict">✕</span> Réinitialiser
          </button>
          <button className="btn" type="button" onClick={() => setRefreshKey((n) => n + 1)}>
            <span className="pict">⟳</span> Actualiser
          </button>
        </div>
      </div>

      <div className="filters">
        <label className="field">
          <span>Projet</span>
          <select className="inp" value={project} onChange={(e) => onProject(e.target.value)}>
            {projectOptions.map((p) => (
              <option key={p.key} value={p.key}>
                {p.key} — {p.name}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span>Sprint</span>
          <select className="inp" value={sprint} onChange={(e) => setSprint(e.target.value)} disabled={!facets}>
            <option value="">Tous les sprints</option>
            <option value={NO_SPRINT}>Sans sprint (backlog){facets ? ` · ${facets.backlog_only}` : ""}</option>
            {items.map((s) => (
              <option key={String(s.id)} value={String(s.id)}>
                {shorten(s.name, 40)} · {SPRINT_LABEL[s.state] ?? s.state} ({s.issues})
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span>Fonctionnalité Jira</span>
          <select
            className="inp"
            value={feature}
            onChange={(e) => {
              setFeature(e.target.value);
              setStory("");
            }}
            disabled={!facets}
          >
            <option value="">Toutes les fonctionnalités</option>
            {(facets?.features ?? []).map((f) => (
              <option key={f.key} value={f.key}>
                {f.key} — {shorten(f.summary, 44)} ({f.issues})
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span>User story Jira</span>
          <select className="inp" value={story} onChange={(e) => setStory(e.target.value)} disabled={!facets}>
            <option value="">Toutes les user stories</option>
            {storyOptions.map((s) => (
              <option key={s.key} value={s.key}>
                {s.key} — {shorten(s.summary, 44)}
              </option>
            ))}
          </select>
        </label>
      </div>

      {data.state === "error" && (
        <ErrorLine>
          {data.error} — la lecture des sprints utilise l'API Agile de Jira ; vérifiez les droits du compte de
          service.
        </ErrorLine>
      )}

      {data.state === "ok" && data.data.board_supports_sprints === false && (
        <Note>
          Ce board Jira ne gère pas de sprints (board Kanban) : la liste est reconstruite depuis le champ
          Sprint des tickets, et tout ce qui n'est pas planifié reste dans « Sans sprint ».
        </Note>
      )}

      <div className="cards">
        <Kpi
          icon="◷"
          label="Sprints"
          value={data.state === "ok" ? String(data.data.count) : "…"}
          hint={`projet ${project}`}
        />
        <Kpi
          icon="▶"
          label="Sprint actif"
          value={active ? shorten(active.name.replace(/^Sprint\s*/i, "S"), 14) : "aucun"}
          hint={active ? `${active.all_issues ?? active.issues} tickets` : "aucun sprint ouvert dans Jira"}
          tone={active ? "ok" : "warn"}
        />
        <Kpi
          icon="▤"
          label="Sélection"
          value={data.state === "ok" ? String(data.data.matching) : "…"}
          hint={data.state === "ok" && data.data.filters.active ? `${data.data.filters.active} filtre(s) actif(s)` : "sans filtre"}
        />
        <Kpi
          icon="◔"
          label="Sans sprint"
          value={data.state === "ok" ? String(data.data.unscheduled) : "…"}
          hint="tickets en backlog"
          tone={data.state === "ok" && data.data.unscheduled > 0 ? "warn" : "flat"}
        />
      </div>

      <Panel
        title="Sprints du board"
        action={<Tag tone="mut">{items.length} affiché(s)</Tag>}
      >
        {data.state === "loading" ? (
          <Loading rows={4} />
        ) : items.length === 0 ? (
          <Empty>Aucun sprint sur ce board pour ce projet.</Empty>
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th>Sprint</th>
                <th>État</th>
                <th>Début</th>
                <th>Fin</th>
                <th>Tickets (sélection / total)</th>
                <th>Avancement</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((s) => {
                const total = s.all_issues ?? s.issues ?? 0;
                const done = s.done ?? 0;
                const pct = total > 0 ? (done / total) * 100 : 0;
                const id = String(s.id);
                return (
                  <tr key={id}>
                    <td>
                      <b>{shorten(s.name, 40)}</b>
                      <div className="mono">#{id}</div>
                    </td>
                    <td>
                      <Tag tone={SPRINT_TONE[s.state] ?? statusTone(s.state)}>
                        {SPRINT_LABEL[s.state] ?? s.state}
                      </Tag>
                    </td>
                    <td className="mono">{s.start ?? "—"}</td>
                    <td className="mono">{s.end ?? "—"}</td>
                    <td className="mono">
                      {s.issues} / {total}
                    </td>
                    <td style={{ minWidth: 130 }}>
                      <Meter value={pct} label={`${done} fait(s)`} hint={`${Math.round(pct)} %`} />
                    </td>
                    <td>
                      <button
                        className="btn ghost sm"
                        type="button"
                        onClick={() => setSprint(id)}
                        disabled={sprint === id}
                      >
                        <span className="pict">▤</span> Tickets
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Panel>

      <Panel
        title={scopeLabel ? `Tickets · ${scopeLabel}` : "Tickets de la sélection"}
        action={
          <span className="chip">
            {tickets.length} ticket(s){feature ? ` · ${feature}` : ""}
            {story ? ` · ${story}` : ""}
          </span>
        }
      >
        {data.state === "loading" ? (
          <Loading rows={3} />
        ) : tickets.length === 0 ? (
          <div className="empty">
            Aucun ticket ne correspond à cette sélection.{" "}
            <button className="link" type="button" onClick={reset}>
              Réinitialiser les filtres
            </button>
          </div>
        ) : (
          <table className="tbl tbl-wide">
            <thead>
              <tr>
                <th>Clé</th>
                <th>Type</th>
                <th>Résumé</th>
                <th>Statut</th>
                <th>Assigné</th>
                <th>Sprint</th>
              </tr>
            </thead>
            <tbody>
              {tickets.map((issue) => (
                <tr key={issue.key}>
                  <td className="mono">
                    {issue.url ? (
                      <a href={issue.url} target="_blank" rel="noreferrer">
                        {issue.key}
                      </a>
                    ) : (
                      issue.key
                    )}
                  </td>
                  <td className="mono">{issue.type ?? "—"}</td>
                  <td className="col-summary">{issue.summary}</td>
                  <td>
                    <Tag tone={statusTone((issue.status ?? "").toLowerCase())}>{issue.status ?? "—"}</Tag>
                  </td>
                  <td className="mono">{issue.assignee ?? "—"}</td>
                  <td className="mono">
                    {(issue.sprints ?? []).map((s) => s.name).join(", ") || "backlog"}
                  </td>
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
      </Panel>
    </div>
  );
}
