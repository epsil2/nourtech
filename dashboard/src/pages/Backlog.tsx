import { useEffect, useMemo, useRef, useState } from "react";
import { Empty, ErrorLine, Kpi, Loading, Panel, Tag, ago, statusTone } from "../components/ui";
import { useAsync } from "../lib/useAsync";
import { board } from "../lib/integrations";
import type { BacklogFilters, BoardIssue } from "../lib/integrations";

const DEFAULT_PROJECT = "SCRUM";
const NO_SPRINT = "none";
const STORE_KEY = "camilia.backlog.project";

const rememberProject = (): string => {
  try {
    return localStorage.getItem(STORE_KEY) || DEFAULT_PROJECT;
  } catch {
    return DEFAULT_PROJECT;
  }
};

const shorten = (text: string, max = 70) =>
  text.length > max ? `${text.slice(0, max - 1)}…` : text;

export function Backlog() {
  const [project, setProject] = useState<string>(rememberProject);
  const [sprint, setSprint] = useState("");
  const [feature, setFeature] = useState("");
  const [story, setStory] = useState("");
  const [needle, setNeedle] = useState("");
  const [query, setQuery] = useState("");

  // one server fetch per settled selection: the search box waits for a pause,
  // the drop-downs apply at once
  useEffect(() => {
    const timer = setTimeout(() => setQuery(needle.trim()), 250);
    return () => clearTimeout(timer);
  }, [needle]);

  const forceFresh = useRef(false);
  const filters: BacklogFilters = { sprint, feature, story, q: query };
  const [data, refresh] = useAsync(() => {
    const fresh = forceFresh.current;
    forceFresh.current = false;
    return board.backlog(project, filters, 300, fresh);
  }, [project, sprint, feature, story, query]);

  const [projects] = useAsync(() => board.projects(), []);

  useEffect(() => {
    try {
      localStorage.setItem(STORE_KEY, project);
    } catch {
      /* private mode: the choice simply is not remembered */
    }
  }, [project]);

  // a selection that no longer exists in Jira must not keep the table empty
  useEffect(() => {
    if (data.state !== "ok") return;
    const f = data.data.facets;
    if (feature && !f.features.some((x) => x.key === feature)) setFeature("");
    if (story && !f.stories.some((s) => s.key === story)) setStory("");
    if (sprint && sprint !== NO_SPRINT && !f.sprints.some((s) => s.id === sprint)) setSprint("");
  }, [data, feature, story, sprint]);

  const facets = data.state === "ok" ? data.data.facets : null;
  const items: BoardIssue[] = data.state === "ok" ? data.data.items : [];
  const counts = data.state === "ok" ? data.data.by_status : {};
  const total = data.state === "ok" ? data.data.total : 0;
  const activeFilters = [sprint, feature, story, query].filter(Boolean).length;

  // the story list follows the chosen feature: a story is picked inside a feature
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
    // sprint, feature and story keys belong to the previous project
    setSprint("");
    setFeature("");
    setStory("");
  };

  const onFeature = (value: string) => {
    setFeature(value);
    if (value && story) {
      const parent = facets?.stories.find((s) => s.key === story)?.parent;
      if (parent !== value) setStory("");
    }
  };

  const reset = () => {
    setSprint("");
    setFeature("");
    setStory("");
    setNeedle("");
    setQuery("");
  };

  const reload = () => {
    forceFresh.current = true;
    refresh();
  };

  if (data.state === "error") {
    return (
      <ErrorLine>
        {data.error}
        {data.error.includes("503") || data.error.toLowerCase().includes("jira")
          ? " — vérifiez que JIRA_SITE_URL, JIRA_ACCOUNT_EMAIL et JIRA_API_TOKEN sont présents dans l'environnement du service."
          : ""}
      </ErrorLine>
    );
  }

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Backlog Jira</h1>
          <p className="sub">
            Lu en direct depuis Jira, filtré par projet, sprint, fonctionnalité et user story. Cette page ne
            crée, ne déplace et ne ferme rien : la création de tickets reste soumise à votre approbation du
            backlog.
          </p>
        </div>
        <div className="head-right">
          <button className="btn" onClick={reload} disabled={data.state === "loading"} type="button">
            {data.state === "loading" ? "Chargement…" : "Actualiser"}
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
          <select
            className="inp"
            value={sprint}
            onChange={(e) => setSprint(e.target.value)}
            disabled={!facets}
          >
            <option value="">Tous les sprints</option>
            <option value={NO_SPRINT}>
              Sans sprint (backlog){facets ? ` · ${facets.backlog_only}` : ""}
            </option>
            {(facets?.sprints ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {shorten(s.name, 46)} · {s.state} ({s.issues})
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span>Fonctionnalité Jira</span>
          <select
            className="inp"
            value={feature}
            onChange={(e) => onFeature(e.target.value)}
            disabled={!facets}
          >
            <option value="">Toutes les fonctionnalités</option>
            {(facets?.features ?? []).map((f) => (
              <option key={f.key} value={f.key}>
                {f.key} — {shorten(f.summary, 48)} ({f.issues})
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span>User story Jira</span>
          <select
            className="inp"
            value={story}
            onChange={(e) => setStory(e.target.value)}
            disabled={!facets}
          >
            <option value="">Toutes les user stories</option>
            {storyOptions.map((s) => (
              <option key={s.key} value={s.key}>
                {s.key} — {shorten(s.summary, 48)}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span>Recherche</span>
          <input
            className="inp"
            placeholder="clé, résumé, statut, assigné"
            value={needle}
            onChange={(e) => setNeedle(e.target.value)}
          />
        </label>

        <div className="field filter-reset">
          <span>Filtres</span>
          <button className="btn" onClick={reset} disabled={!activeFilters} type="button">
            Réinitialiser{activeFilters ? ` (${activeFilters})` : ""}
          </button>
        </div>
      </div>

      <div className="cards">
        <Kpi
          icon="▤"
          label="Tickets"
          value={data.state === "ok" ? String(total) : "…"}
          hint={`projet ${project}`}
        />
        <Kpi
          icon="◔"
          label="Restant"
          value={String(Object.entries(counts).filter(([s]) => s !== "Done").reduce((n, [, v]) => n + v, 0))}
          hint="sélection, hors Done"
        />
        <Kpi
          icon="✎"
          label="Affichés"
          value={String(items.length)}
          hint={activeFilters ? `${activeFilters} filtre(s) actif(s)` : "aucun filtre"}
          tone="flat"
        />
      </div>

      <Panel
        title="Tickets"
        action={
          <div className="chips">
            {Object.entries(counts).map(([status, n]) => (
              <Tag key={status} tone={statusTone(status)}>
                {status} {n}
              </Tag>
            ))}
          </div>
        }
      >
        {data.state === "loading" && !items.length ? (
          <Loading rows={5} />
        ) : items.length === 0 ? (
          <Empty>
            Aucun ticket ne correspond à cette sélection
            {activeFilters ? ` (${activeFilters} filtre(s) actif(s))` : ""}. Un ticket ne peut pas être à la
            fois dans le sprint choisi et dans la user story choisie si celle-ci est planifiée ailleurs —
            élargissez le sprint ou retirez la user story.
            {activeFilters ? (
              <div style={{ marginTop: 10 }}>
                <button className="btn sm" onClick={reset} type="button">
                  Réinitialiser les filtres
                </button>
              </div>
            ) : null}
          </Empty>
        ) : (
          <table className="tbl tbl-wide">
            <thead>
              <tr>
                <th>Clé</th>
                <th>Type</th>
                <th>Résumé</th>
                <th>Statut</th>
                <th>Assigné</th>
                <th>Parent</th>
                <th>Sprint</th>
                <th>Maj</th>
              </tr>
            </thead>
            <tbody>
              {items.map((i) => (
                <tr key={i.key}>
                  <td className="mono">
                    {i.url ? (
                      <a href={i.url} target="_blank" rel="noreferrer">
                        {i.key}
                      </a>
                    ) : (
                      i.key
                    )}
                  </td>
                  <td>{i.type ?? "—"}</td>
                  <td className="col-summary">{i.summary}</td>
                  <td>
                    <Tag tone={statusTone(i.status ?? "")}>{i.status ?? "—"}</Tag>
                  </td>
                  <td>{i.assignee ?? "non assigné"}</td>
                  <td className="mono">{i.parent ?? "—"}</td>
                  <td>{i.sprints.length ? i.sprints.map((s) => s.name).join(", ") : "backlog"}</td>
                  <td className="mono">{i.updated ? ago(i.updated) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {data.state === "ok" && data.data.truncated ? (
          <p className="foot-note">
            Projet plus grand que la limite de lecture : seuls les {data.data.total} tickets les plus récents
            sont comptés ici.
          </p>
        ) : null}
      </Panel>
    </div>
  );
}
