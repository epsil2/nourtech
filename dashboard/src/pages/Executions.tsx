import { useEffect, useMemo, useState } from "react";
import { Empty, ErrorLine, Kpi, Loading, Panel, Tag, ago, statusTone } from "../components/ui";
import { actor } from "../lib/api";
import { cp } from "../lib/cp";
import type { AgentSummary, Execution } from "../lib/cp";
import { board } from "../lib/integrations";
import type { BacklogFacets } from "../lib/integrations";
import { useAsync } from "../lib/useAsync";

const NO_SPRINT = "none";

function shorten(text: string, max = 46): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

const BLANK = {
  project_id: "jira:SCRUM",
  agent_id: "",
  objective: "",
  jira_key: "",
  sprint_id: "",
  sprint_name: "",
  feature_key: "",
  story_key: "",
  dispatch: true,
};

export function Executions() {
  const [engine] = useAsync(() => cp.engine());
  const [deploymentProjects] = useAsync(() => cp.deploymentProjects());
  const [agents] = useAsync(() => cp.agents());
  const [jiraGate] = useAsync(() => cp.jiraWrite());

  const [scope, setScope] = useState({ project: "SCRUM", sprint: "", feature: "", story: "", q: "" });
  const [refreshKey, setRefreshKey] = useState(0);

  const [list, refreshList] = useAsync(
    () =>
      cp.executions({
        projectId: scope.project === "SCRUM" ? undefined : `jira:${scope.project}`,
        sprint: scope.sprint && scope.sprint !== NO_SPRINT ? scope.sprint : undefined,
        feature: scope.feature || undefined,
        story: scope.story || undefined,
        q: scope.q || undefined,
      }),
    [scope, refreshKey],
  );

  const [facets] = useAsync(() => board.backlog(scope.project, {}, 300, refreshKey > 0), [
    scope.project,
    refreshKey,
  ]);

  const [selected, setSelected] = useState<string | null>(null);
  const [form, setForm] = useState({ ...BLANK });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [detail, refreshDetail] = useAsync(
    () => (selected ? cp.execution(selected) : Promise.resolve(null)),
    [selected, refreshKey],
  );

  useEffect(() => {
    if (!selected) return;
    const timer = setInterval(() => {
      cp.syncExecution(selected)
        .then(() => refreshDetail())
        .catch(() => undefined);
    }, 15000);
    return () => clearInterval(timer);
  }, [selected, refreshDetail]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const start = () =>
    run(async () => {
      const story = form.story_key
        ? (facets.state === "ok" ? facets.data.items.find((i) => i.key === form.story_key) : undefined)
        : undefined;
      const objective =
        form.objective.trim() ||
        (story ? `${form.story_key} — ${story.summary}` : `Traiter ${form.jira_key || "la sélection"}`);
      const execution = await cp.run({
        project_id: form.project_id,
        objective,
        jira_key: form.jira_key || form.story_key || form.feature_key || undefined,
        agent_id: form.agent_id || undefined,
        sprint_id: form.sprint_id && form.sprint_id !== NO_SPRINT ? form.sprint_id : undefined,
        sprint_name: form.sprint_name || undefined,
        feature_key: form.feature_key || undefined,
        story_key: form.story_key || undefined,
        dispatch: form.dispatch,
      });
      setSelected(execution.id);
      setNotice(`Exécution créée — tâche Hermes ${execution.task_id ?? "en cours d'attribution"}.`);
      setForm({ ...form, objective: "" });
      refreshList();
    });

  const data = detail.state === "ok" ? detail.data : null;
  const executions: Execution[] = list.state === "ok" ? list.data.executions : [];
  const warnings = list.state === "ok" ? list.data.warnings ?? [] : [];

  const agentOptions = useMemo<AgentSummary[]>(() => {
    if (agents.state !== "ok") return [];
    return agents.data.agents ?? agents.data.items ?? [];
  }, [agents]);

  const facetData: BacklogFacets | null = facets.state === "ok" ? facets.data.facets : null;
  const sprintChoices = facetData?.sprints ?? [];
  const featureChoices = facetData?.features ?? [];
  const storyChoices = useMemo(
    () => (facetData ? (form.feature_key ? facetData.stories.filter((s) => s.parent === form.feature_key) : facetData.stories) : []),
    [facetData, form.feature_key],
  );

  const projectOptions = deploymentProjects.state === "ok" ? deploymentProjects.data.projects : [];
  const running = executions.filter((e) => ["running", "queued", "scheduled", "starting"].includes(e.status)).length;
  const jiraDone = executions.filter((e) => (e.jira_status ?? "").toLowerCase().match(/done|termin|clos/)).length;
  const available = engine.state === "ok" ? engine.data.available : null;

  const resetScope = () => setScope({ ...scope, sprint: "", feature: "", story: "", q: "" });

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Exécutions</h1>
          <p className="sub">
            Camilia pilote, l'agent exécute. Choisissez le projet, le sprint, la fonctionnalité puis la user story,
            puis l'agent qui prend la tâche — Hermes Agent par défaut, ou n'importe quel agent de la section
            <b> Agents</b>. Le statut Jira est relu et mis à jour à chaque synchronisation.
          </p>
        </div>
        <div className="head-right">
          <button className="btn ghost" type="button" onClick={resetScope}>
            <span className="pict">✕</span> Réinitialiser
          </button>
          <button className="btn" type="button" onClick={() => setRefreshKey((n) => n + 1)}>
            <span className="pict">⟳</span> Actualiser
          </button>
        </div>
      </div>

      {engine.state === "ok" && !engine.data.available && (
        <ErrorLine>
          Le moteur Hermes n'est pas joignable sur cet hôte : aucune exécution ne peut être créée. La page reste en
          lecture seule plutôt que d'afficher des exécutions simulées.
        </ErrorLine>
      )}
      {engine.state === "ok" && engine.data.available && (
        <p className="foot-note">
          moteur <b>{engine.data.engine}</b> · binaire <span className="mono">{engine.data.binary}</span>
          {jiraGate.state === "ok" &&
            (jiraGate.data.write_enabled ? (
              <>
                {" "}
                · écriture Jira <b>activée</b> ({jiraGate.data.write_projects.join(", ") || "aucun projet autorisé"})
              </>
            ) : (
              <>
                {" "}
                · écriture Jira <b>désactivée</b> : le statut est relu et affiché, mais le ticket n'est pas déplacé
                (JIRA_WRITE_ENABLED)
              </>
            ))}
        </p>
      )}

      <div className="filters">
        <label className="field">
          <span>Projet</span>
          <select
            className="inp"
            value={scope.project}
            onChange={(e) => setScope({ ...scope, project: e.target.value, sprint: "", feature: "", story: "" })}
          >
            {(projectOptions.length ? projectOptions : [{ key: "SCRUM", id: "jira:SCRUM", name: "SCRUM", jira_project: "SCRUM", source: "jira" as const, board: null }]).map(
              (project) => (
                <option key={project.key} value={project.key}>
                  {project.key} — {project.name}
                </option>
              ),
            )}
          </select>
        </label>

        <label className="field">
          <span>Sprint</span>
          <select className="inp" value={scope.sprint} onChange={(e) => setScope({ ...scope, sprint: e.target.value })}>
            <option value="">Tous les sprints</option>
            <option value={NO_SPRINT}>Sans sprint (backlog)</option>
            {sprintChoices.map((sprint) => (
              <option key={sprint.id} value={String(sprint.id)}>
                {shorten(sprint.name, 38)} ({sprint.issues})
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span>Fonctionnalité Jira</span>
          <select
            className="inp"
            value={scope.feature}
            onChange={(e) => setScope({ ...scope, feature: e.target.value, story: "" })}
          >
            <option value="">Toutes les fonctionnalités</option>
            {featureChoices.map((feature) => (
              <option key={feature.key} value={feature.key}>
                {feature.key} — {shorten(feature.summary, 40)} ({feature.issues})
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span>User story Jira</span>
          <select className="inp" value={scope.story} onChange={(e) => setScope({ ...scope, story: e.target.value })}>
            <option value="">Toutes les user stories</option>
            {storyChoices.map((story) => (
              <option key={story.key} value={story.key}>
                {story.key} — {shorten(story.summary, 40)}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span>Recherche</span>
          <input
            className="inp"
            value={scope.q}
            onChange={(e) => setScope({ ...scope, q: e.target.value })}
            placeholder="objectif, ticket, agent"
          />
        </label>
      </div>

      {warnings.length > 0 && <p className="foot-note">⚠ {warnings.join(" · ")}</p>}
      {error && <ErrorLine>{error}</ErrorLine>}
      {notice && <div className="tag t-info">{notice}</div>}

      <div className="cards">
        <Kpi icon="▶" label="Exécutions" value={String(executions.length)} hint="dans la sélection" />
        <Kpi icon="↻" label="En cours" value={String(running)} hint="running / queued" tone={running ? "warn" : "flat"} />
        <Kpi
          icon="✓"
          label="Réussies"
          value={String(executions.filter((e) => e.status === "succeeded").length)}
          hint="statut lu depuis Hermes"
        />
        <Kpi icon="◈" label="Jira terminés" value={String(jiraDone)} hint="statut Jira relu" tone="flat" />
      </div>

      <div className="grid-2">
        <Panel title="Tâches" action={<span className="chip">{executions.length} ligne(s)</span>}>
          {list.state === "loading" && <Loading rows={3} />}
          {list.state === "error" && <ErrorLine>{list.error}</ErrorLine>}
          {list.state === "ok" && executions.length === 0 && (
            <div className="empty">Aucune exécution pour cette sélection.</div>
          )}
          {executions.length > 0 && (
            <table className="tbl tbl-wide">
              <thead>
                <tr>
                  <th>Objectif</th>
                  <th>Jira</th>
                  <th>Statut Jira</th>
                  <th>Statut</th>
                  <th>Agent</th>
                  <th>Créée</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {executions.map((execution) => (
                  <tr key={execution.id}>
                    <td className="col-summary">{shorten(execution.objective, 70)}</td>
                    <td className="mono">{execution.jira_key ?? "—"}</td>
                    <td>
                      {execution.jira_status ? (
                        <Tag tone={statusTone(execution.jira_status.toLowerCase())}>{execution.jira_status}</Tag>
                      ) : (
                        <span className="mono">—</span>
                      )}
                    </td>
                    <td>
                      <Tag tone={statusTone(execution.status)}>{execution.status}</Tag>
                    </td>
                    <td className="mono">{execution.agent_id ? shorten(execution.agent_id, 14) : "hermes"}</td>
                    <td className="mono">{ago(execution.created_at)}</td>
                    <td>
                      <button className="btn ghost sm" onClick={() => setSelected(execution.id)} type="button">
                        <span className="pict">◉</span> Détail
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>

        <Panel title="Lancer une exécution">
          <label className="field">
            <span>Agent</span>
            <select className="inp" value={form.agent_id} onChange={(e) => setForm({ ...form, agent_id: e.target.value })}>
              <option value="">Hermes Agent (défaut)</option>
              {agentOptions.map((agent) => (
                <option key={agent.id} value={agent.id}>
                  {agent.name}
                  {agent.model ? ` · ${agent.model}` : ""}
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            <span>Projet</span>
            <select
              className="inp"
              value={form.project_id}
              onChange={(e) => setForm({ ...form, project_id: e.target.value, sprint_id: "", feature_key: "", story_key: "", jira_key: "" })}
            >
              <option value="jira:SCRUM">jira:SCRUM — projet Jira</option>
              {projectOptions.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.key} · {project.name}
                </option>
              ))}
            </select>
          </label>

          <div className="grid-2">
            <label className="field">
              <span>Sprint</span>
              <select
                className="inp"
                value={form.sprint_id}
                onChange={(e) => {
                  const chosen = sprintChoices.find((s) => String(s.id) === e.target.value);
                  setForm({ ...form, sprint_id: e.target.value, sprint_name: chosen?.name ?? "" });
                }}
              >
                <option value="">Sans sprint</option>
                {sprintChoices.map((sprint) => (
                  <option key={sprint.id} value={String(sprint.id)}>
                    {shorten(sprint.name, 30)}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Fonctionnalité</span>
              <select
                className="inp"
                value={form.feature_key}
                onChange={(e) => setForm({ ...form, feature_key: e.target.value, story_key: "" })}
              >
                <option value="">—</option>
                {featureChoices.map((feature) => (
                  <option key={feature.key} value={feature.key}>
                    {feature.key}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <label className="field">
            <span>User story</span>
            <select
              className="inp"
              value={form.story_key}
              onChange={(e) => {
                const chosen = storyChoices.find((s) => s.key === e.target.value);
                setForm({ ...form, story_key: e.target.value, jira_key: e.target.value });
                if (chosen && !form.objective.trim()) setForm((prev) => ({ ...prev, objective: `${chosen.key} — ${chosen.summary}` }));
              }}
            >
              <option value="">—</option>
              {storyChoices.map((story) => (
                <option key={story.key} value={story.key}>
                  {story.key} — {shorten(story.summary, 34)}
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            <span>Ticket Jira</span>
            <input
              className="inp"
              value={form.jira_key}
              onChange={(e) => setForm({ ...form, jira_key: e.target.value })}
              placeholder="SCRUM-123"
            />
          </label>

          <label className="field">
            <span>Objectif</span>
            <textarea
              className="inp"
              rows={3}
              value={form.objective}
              onChange={(e) => setForm({ ...form, objective: e.target.value })}
              placeholder="Ce que l'agent doit produire, avec les preuves attendues."
            />
          </label>

          <label className="sub" style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input
              type="checkbox"
              checked={form.dispatch}
              onChange={(e) => setForm({ ...form, dispatch: e.target.checked })}
            />
            lancer immédiatement (dispatch)
          </label>

          <button
            className="btn"
            onClick={start}
            disabled={busy || available === false || !form.project_id || (!form.objective.trim() && !form.jira_key)}
            type="button"
            style={{ marginTop: 10, width: "100%", justifyContent: "center" }}
          >
            <span className="pict">▶</span> {busy ? "Création…" : "Lancer l'exécution"}
          </button>
          <p className="foot-note" style={{ marginTop: 8 }}>
            La tâche est créée avec une clé d'idempotence, une durée maximale et un nombre d'essais bornés. Le ticket
            Jira passe « en cours » au lancement puis « terminé » à la réussite, quand le workflow l'autorise.
          </p>
        </Panel>
      </div>

      {selected && data && (
        <Panel
          title={`Exécution ${data.execution.id.slice(0, 8)} — ${data.execution.status}`}
          action={
            <div className="head-right">
              <button
                className="btn ghost sm"
                onClick={() => run(async () => { await cp.syncExecution(data.execution.id); refreshDetail(); refreshList(); })}
                disabled={busy}
                type="button"
              >
                <span className="pict">⟳</span> Synchroniser
              </button>
              <button
                className="btn ghost sm"
                onClick={() =>
                  run(async () => {
                    const reason = window.prompt("Motif de l'arrêt ?", "arrêt demandé depuis Camilia");
                    if (!reason) return;
                    await cp.cancelExecution(data.execution.id, reason);
                    refreshDetail();
                    refreshList();
                  })
                }
                disabled={busy}
                type="button"
              >
                <span className="pict">■</span> Arrêter
              </button>
            </div>
          }
        >
          <p className="sub">{data.execution.objective}</p>

          <div className="grid-2">
            <div>
              <div className="kv">
                <span>Board Hermes</span>
                <b className="mono">{data.execution.board ?? "—"}</b>
              </div>
              <div className="kv">
                <span>Tâche</span>
                <b className="mono">{data.execution.task_id ?? "—"}</b>
              </div>
              <div className="kv">
                <span>Runs</span>
                <b>{data.execution.runs ?? 0}</b>
              </div>
              <div className="kv">
                <span>Acteur</span>
                <b>{actor()}</b>
              </div>
              <div className="kv">
                <span>Agent</span>
                <b>{data.execution.agent_id ?? "Hermes Agent (défaut)"}</b>
              </div>
            </div>
            <div>
              <div className="kv">
                <span>Ticket Jira</span>
                <b className="mono">
                  {data.jira?.url ? (
                    <a href={data.jira.url} target="_blank" rel="noreferrer">
                      {data.jira.key}
                    </a>
                  ) : (
                    data.execution.jira_key ?? "—"
                  )}
                </b>
              </div>
              <div className="kv">
                <span>Statut Jira</span>
                <b>
                  {data.jira?.status ? (
                    <Tag tone={statusTone(data.jira.status.toLowerCase())}>{data.jira.status}</Tag>
                  ) : (
                    <span className="mono">{data.jira?.error ? "indisponible" : "—"}</span>
                  )}
                </b>
              </div>
              <div className="kv">
                <span>Assigné Jira</span>
                <b>{data.jira?.assignee ?? "—"}</b>
              </div>
              <div className="kv">
                <span>Sprint</span>
                <b>{data.execution.sprint_name ?? data.execution.sprint_id ?? "—"}</b>
              </div>
              <div className="kv">
                <span>User story</span>
                <b className="mono">{data.execution.story_key ?? "—"}</b>
              </div>
            </div>
          </div>

          {data.execution.error && <ErrorLine>{data.execution.error}</ErrorLine>}

          <table className="tbl" style={{ marginTop: 10 }}>
            <thead>
              <tr>
                <th>Quand</th>
                <th>Type</th>
                <th>Message</th>
              </tr>
            </thead>
            <tbody>
              {data.events.map((event) => (
                <tr key={event.id}>
                  <td className="mono">{ago(event.created_at)}</td>
                  <td className="mono">{event.kind}</td>
                  <td>{event.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.events.length === 0 && <Empty>Aucun évènement pour cette exécution.</Empty>}
        </Panel>
      )}
    </div>
  );
}
