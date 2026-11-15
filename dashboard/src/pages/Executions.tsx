import { useEffect, useState } from "react";
import { Empty, ErrorLine, Kpi, Loading, Panel, Tag, ago, statusTone } from "../components/ui";
import { actor } from "../lib/api";
import { cp } from "../lib/cp";
import { useAsync } from "../lib/useAsync";

export function Executions() {
  const [engine, refreshEngine] = useAsync(() => cp.engine());
  const [projects] = useAsync(() => cp.projects());
  const [list, refreshList] = useAsync(() => cp.executions());
  const [selected, setSelected] = useState<string | null>(null);
  const [form, setForm] = useState({ project_id: "", objective: "", jira_key: "", dispatch: true });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [detail, refreshDetail] = useAsync(
    () => (selected ? cp.execution(selected) : Promise.resolve(null)),
    [selected],
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
      const execution = await cp.run({
        project_id: form.project_id,
        objective: form.objective.trim(),
        jira_key: form.jira_key || undefined,
        dispatch: form.dispatch,
      });
      setSelected(execution.id);
      setForm({ ...form, objective: "", jira_key: "" });
      setNotice(`Exécution créée — tâche Hermes ${execution.task_id ?? "en cours d'attribution"}.`);
      refreshList();
    });

  const available = engine.state === "ok" ? engine.data.available : null;
  const data = detail.state === "ok" ? detail.data : null;
  const executions = list.state === "ok" ? list.data.executions : [];
  const projectList = projects.state === "ok" ? projects.data.projects : [];
  const running = executions.filter((e) => ["running", "queued", "scheduled", "starting"].includes(e.status)).length;

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Exécutions</h1>
          <p className="sub">
            Camilia pilote, Hermes exécute. Chaque ligne correspond à une tâche réelle du moteur, avec son identifiant de
            board et ses preuves.
          </p>
        </div>
        <div className="head-right">
          <button
            className="btn ghost"
            onClick={() => {
              refreshEngine();
              refreshList();
            }}
            type="button"
          >
            Actualiser
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
        <p className="sub">
          moteur <b>{engine.data.engine}</b> · binaire <span className="mono">{engine.data.binary}</span>
        </p>
      )}

      {error && <ErrorLine>{error}</ErrorLine>}
      {notice && <div className="tag t-info">{notice}</div>}

      <div className="cards">
        <Kpi icon="▶" label="Exécutions" value={String(executions.length)} hint="enregistrées" />
        <Kpi icon="↻" label="En cours" value={String(running)} hint="running / queued" tone={running ? "warn" : "flat"} />
        <Kpi
          icon="✓"
          label="Réussies"
          value={String(executions.filter((e) => e.status === "succeeded").length)}
          hint="statut lu depuis Hermes"
        />
        <Kpi
          icon="✕"
          label="Bloquées"
          value={String(executions.filter((e) => ["blocked", "failed", "cancelled"].includes(e.status)).length)}
          hint="failed / blocked / annulées"
          tone={executions.some((e) => e.status === "failed") ? "warn" : "flat"}
        />
      </div>

      <div className="grid-2">
        <Panel title="Tâches">
          {list.state === "loading" && <Loading rows={3} />}
          {list.state === "error" && <ErrorLine>{list.error}</ErrorLine>}
          {list.state === "ok" && executions.length === 0 && <Empty>Aucune exécution enregistrée.</Empty>}
          {executions.length > 0 && (
            <table className="tbl">
              <thead>
                <tr>
                  <th>Objectif</th>
                  <th>Projet</th>
                  <th>Statut</th>
                  <th>Tâche</th>
                  <th>Créée</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {executions.map((execution) => (
                  <tr key={execution.id}>
                    <td>{execution.objective.slice(0, 70)}</td>
                    <td className="mono">{execution.project_key ?? "—"}</td>
                    <td>
                      <Tag tone={statusTone(execution.status)}>{execution.status}</Tag>
                    </td>
                    <td className="mono">{execution.task_id ?? "—"}</td>
                    <td>{ago(execution.created_at)}</td>
                    <td>
                      <button className="btn ghost sm" onClick={() => setSelected(execution.id)} type="button">
                        Détail
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>

        <Panel title="Lancer une exécution">
          <div className="field">
            <span>Projet</span>
            <select
              value={form.project_id}
              onChange={(e) => setForm({ ...form, project_id: e.target.value })}
            >
              <option value="">— choisir —</option>
              {projectList.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.key} · {project.name}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <span>Objectif</span>
            <textarea
              rows={3}
              value={form.objective}
              onChange={(e) => setForm({ ...form, objective: e.target.value })}
              placeholder="Ce que l'agent doit produire, avec les preuves attendues."
            />
          </div>
          <div className="field">
            <span>Ticket Jira (optionnel)</span>
            <input value={form.jira_key} onChange={(e) => setForm({ ...form, jira_key: e.target.value })} placeholder="SCRUM-123" />
          </div>
          <label className="sub">
            <input
              type="checkbox"
              checked={form.dispatch}
              onChange={(e) => setForm({ ...form, dispatch: e.target.checked })}
            />{" "}
            lancer immédiatement (dispatch)
          </label>
          <p className="sub">
            La tâche est créée avec une clé d'idempotence, une durée maximale et un nombre d'essais bornés. Sans
            dispatch, elle reste en file jusqu'à la prochaine relance du board.
          </p>
          <button
            className="btn"
            onClick={start}
            disabled={busy || available === false || !form.project_id || form.objective.trim().length < 5}
            type="button"
          >
            Créer l'exécution
          </button>
        </Panel>
      </div>

      {selected && data && (
        <Panel
          title={`Exécution ${data.execution.id.slice(0, 8)} — ${data.execution.status}`}
          action={
            <div className="head-right">
              <button
                className="btn ghost sm"
                onClick={() => run(async () => { await cp.syncExecution(data.execution.id); refreshDetail(); })}
                disabled={busy}
                type="button"
              >
                Synchroniser
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
                Arrêter
              </button>
            </div>
          }
        >
          <p className="sub">{data.execution.objective}</p>
          <div className="row">
            <span>board <span className="mono">{data.execution.board ?? "—"}</span></span>
            <span>tâche <span className="mono">{data.execution.task_id ?? "—"}</span></span>
            <span>runs {data.execution.runs ?? 0}</span>
            <span>acteur {actor()}</span>
          </div>
          {data.execution.error && <ErrorLine>{data.execution.error}</ErrorLine>}
          <table className="tbl">
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
                  <td>{ago(event.created_at)}</td>
                  <td className="mono">{event.kind}</td>
                  <td>{event.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
    </div>
  );
}
