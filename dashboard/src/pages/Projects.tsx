import { useEffect, useState } from "react";
import { Empty, ErrorLine, Kpi, Loading, Panel, Tag, ago, statusTone } from "../components/ui";
import { actor } from "../lib/api";
import { cp } from "../lib/cp";
import type { Job, Project } from "../lib/cp";
import { useAsync } from "../lib/useAsync";

const BLANK = { key: "", name: "", business_request: "", jira_project: "SCRUM" };

export function Projects() {
  const [list, refreshList] = useAsync(() => cp.projects());
  const [selected, setSelected] = useState<string | null>(null);
  const [form, setForm] = useState({ ...BLANK });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [detail, refreshDetail] = useAsync(
    () => (selected ? cp.project(selected) : Promise.resolve(null)),
    [selected],
  );
  const [job, setJob] = useState<Job | null>(null);

  useEffect(() => {
    if (!job || job.status === "succeeded" || job.status === "failed") return;
    const timer = setInterval(() => {
      cp.job(`/api/v1/jobs/${job.id}`)
        .then((fresh) => {
          setJob(fresh);
          if (fresh.status === "succeeded" || fresh.status === "failed") refreshDetail();
        })
        .catch(() => setJob({ ...job, status: "failed", error: "suivi du job interrompu" }));
    }, 1500);
    return () => clearInterval(timer);
  }, [job, refreshDetail]);

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

  const create = () =>
    run(async () => {
      const project = await cp.createProject({
        ...form,
        key: form.key.trim().toUpperCase(),
        name: form.name.trim(),
      });
      setForm({ ...BLANK });
      setSelected(project.id);
      refreshList();
    });

  const propose = (project: Project) =>
    run(async () => {
      const proposal = await cp.propose(project.id);
      setNotice(`Proposition créée : ${proposal.items.length} éléments à valider.`);
      refreshDetail();
    });

  const approve = (proposalId: string) =>
    run(async () => {
      await cp.approveProposal(proposalId, actor());
      setNotice(`Proposition approuvée par ${actor()}.`);
      refreshDetail();
    });

  const materialize = (proposalId: string) =>
    run(async () => {
      const started = await cp.materialize(proposalId);
      setJob(started.job);
      setNotice("Création Jira lancée en tâche de fond.");
    });

  const data = detail.state === "ok" ? detail.data : null;
  const projects = list.state === "ok" ? list.data.projects : [];

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Projets</h1>
          <p className="sub">
            Une demande business devient une spécification, un backlog proposé, puis des tickets Jira — chaque étape
            attend une validation humaine.
          </p>
        </div>
        <div className="head-right">
          <button className="btn ghost" onClick={refreshList} type="button">
            Actualiser
          </button>
        </div>
      </div>

      {error && <ErrorLine>{error}</ErrorLine>}
      {notice && <div className="tag t-info">{notice}</div>}

      {list.state === "error" && <ErrorLine>{list.error}</ErrorLine>}
      {list.state === "loading" && <Loading rows={4} />}

      {list.state === "ok" && (
        <div className="cards">
          <Kpi icon="◈" label="Projets" value={String(list.data.total)} hint="créés depuis Camilia" />
          <Kpi
            icon="≡"
            label="Propositions"
            value={String(projects.reduce((sum, p) => sum + (p.counts?.proposals ?? 0), 0))}
            hint="backlogs proposés"
            tone="flat"
          />
          <Kpi
            icon="◇"
            label="Tickets liés"
            value={String(projects.reduce((sum, p) => sum + (p.counts?.issues ?? 0), 0))}
            hint="issues Jira rattachées"
            tone="flat"
          />
          <Kpi
            icon="▶"
            label="Exécutions"
            value={String(projects.reduce((sum, p) => sum + (p.counts?.executions ?? 0), 0))}
            hint="tâches Hermes"
            tone="flat"
          />
        </div>
      )}

      <div className="grid-2">
        <Panel title="Projets">
          {list.state === "ok" && projects.length === 0 && (
            <Empty>Aucun projet. Créez le premier à droite : il porte la demande, les objectifs et le dépôt.</Empty>
          )}
          {projects.length > 0 && (
            <table className="tbl">
              <thead>
                <tr>
                  <th>Clé</th>
                  <th>Nom</th>
                  <th>Statut</th>
                  <th>Backlog</th>
                  <th>Exéc.</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {projects.map((project) => (
                  <tr key={project.id}>
                    <td className="mono">{project.key}</td>
                    <td>{project.name}</td>
                    <td>
                      <Tag tone={statusTone(project.status ?? "draft")}>{project.status ?? "draft"}</Tag>
                    </td>
                    <td>{project.counts?.proposals ?? 0}</td>
                    <td>{project.counts?.executions ?? 0}</td>
                    <td>
                      <button className="btn ghost sm" onClick={() => setSelected(project.id)} type="button">
                        Ouvrir
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>

        <div className="stack">
          <Panel title="Nouveau projet">
            <div className="field">
              <span>Clé (2-10, majuscules)</span>
              <input value={form.key} onChange={(e) => setForm({ ...form, key: e.target.value })} placeholder="VEILLE" />
            </div>
            <div className="field">
              <span>Nom</span>
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Veille IA hebdomadaire" />
            </div>
            <div className="field">
              <span>Demande business</span>
              <textarea
                rows={3}
                value={form.business_request}
                onChange={(e) => setForm({ ...form, business_request: e.target.value })}
                placeholder="Ce que le métier demande, pourquoi, et à quelle échéance."
              />
            </div>
            <div className="field">
              <span>Projet Jira</span>
              <input value={form.jira_project} onChange={(e) => setForm({ ...form, jira_project: e.target.value })} />
            </div>
            <button className="btn" onClick={create} disabled={busy || form.key.length < 2 || form.name.length < 2} type="button">
              Créer le projet
            </button>
          </Panel>

          {job && (
            <Panel title="Création Jira">
              <p className="sub">
                Job <span className="mono">{job.id.slice(0, 8)}</span> · <Tag tone={job.status === "failed" ? "bad" : job.status === "succeeded" ? "ok" : "warn"}>{job.status}</Tag>
              </p>
              {job.error && <ErrorLine>{job.error}</ErrorLine>}
              {job.result && (
                <ul>
                  <li>créés : {job.result.created?.length ?? 0}</li>
                  <li>déjà présents : {job.result.existing?.length ?? 0}</li>
                  <li>échecs : {job.result.failures?.length ?? 0}</li>
                </ul>
              )}
              {!!job.result?.failures?.length && (
                <ErrorLine>
                  {job.result.failures.map((failure) => `${failure.item_key} : ${failure.error}`).join(" · ")}
                </ErrorLine>
              )}
            </Panel>
          )}
        </div>
      </div>

      {selected && (
        <>
          {detail.state === "loading" && <Loading rows={3} />}
          {detail.state === "error" && <ErrorLine>{detail.error}</ErrorLine>}
          {data && (
            <>
              <Panel
                title={`${data.project.key} — ${data.project.name}`}
                action={
                  <div className="head-right">
                    <button className="btn ghost sm" onClick={refreshDetail} type="button">
                      Actualiser
                    </button>
                    <button className="btn sm" onClick={() => propose(data.project)} disabled={busy} type="button">
                      Proposer un backlog
                    </button>
                  </div>
                }
              >
                <p className="sub">{data.project.business_request || "aucune demande renseignée"}</p>
                <div className="row">
                  <span>Jira {data.project.jira_project}</span>
                  <span>{data.project.objectives?.length ?? 0} objectifs</span>
                  <span>{data.issues.length} tickets</span>
                  <span>créé {ago(data.project.created_at)}</span>
                </div>
              </Panel>

              <Panel title="Backlogs proposés">
                {data.proposals.length === 0 && (
                  <Empty>
                    Aucune proposition. « Proposer un backlog » reprend la dernière spécification approuvée et la
                    convertit en Epic / Stories / Sous-tâches — rien n'est créé dans Jira à ce stade.
                  </Empty>
                )}
                {data.proposals.map((proposal) => (
                  <div className="stack" key={proposal.id} style={{ marginBottom: 14 }}>
                    <div className="row">
                      <b>{proposal.title}</b>
                      <Tag tone={statusTone(proposal.status)}>{proposal.status}</Tag>
                      <span>
                        {proposal.summary?.epics ?? 0} epic · {proposal.summary?.stories ?? 0} stories ·{" "}
                        {proposal.summary?.subtasks ?? 0} sous-tâches
                      </span>
                      <span>{ago(proposal.created_at)}</span>
                    </div>
                    <table className="tbl">
                      <thead>
                        <tr>
                          <th>Clé</th>
                          <th>Type</th>
                          <th>Résumé</th>
                          <th>Parent</th>
                        </tr>
                      </thead>
                      <tbody>
                        {proposal.items.slice(0, 12).map((item) => (
                          <tr key={item.key}>
                            <td className="mono">{item.key}</td>
                            <td>{item.type}</td>
                            <td>{item.summary}</td>
                            <td className="mono">{item.parent ?? "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <div className="head-right">
                      {proposal.status === "draft" && (
                        <button className="btn sm" onClick={() => approve(proposal.id)} disabled={busy} type="button">
                          Approuver en tant que {actor()}
                        </button>
                      )}
                      {["approved", "materialised", "failed"].includes(proposal.status) && (
                        <button className="btn sm" onClick={() => materialize(proposal.id)} disabled={busy} type="button">
                          Créer dans Jira
                        </button>
                      )}
                      {proposal.status === "approved" && <span className="sub">approuvé — création Jira en attente</span>}
                    </div>
                    {proposal.result && (
                      <p className="sub">
                        dernier passage : {proposal.result.created} créés · {proposal.result.existing} déjà présents ·{" "}
                        {proposal.result.failed} échecs
                      </p>
                    )}
                  </div>
                ))}
              </Panel>

              <Panel
                title="Tickets Jira"
                action={
                  <button
                    className="btn ghost sm"
                    onClick={() =>
                      run(async () => {
                        const result = await cp.syncJira(data.project.id);
                        setNotice(`${result.checked} tickets vérifiés, ${result.updated} statuts mis à jour.`);
                        refreshDetail();
                      })
                    }
                    disabled={busy || data.issues.length === 0}
                    type="button"
                  >
                    Synchroniser les statuts
                  </button>
                }
              >
                {data.issues.length === 0 && <Empty>Aucun ticket rattaché à ce projet.</Empty>}
                {data.issues.length > 0 && (
                  <table className="tbl">
                    <thead>
                      <tr>
                        <th>Ticket</th>
                        <th>Type</th>
                        <th>Résumé</th>
                        <th>Statut</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.issues.map((issue) => (
                        <tr key={issue.id}>
                          <td className="mono">
                            {issue.url ? (
                              <a href={issue.url} target="_blank" rel="noreferrer">
                                {issue.key}
                              </a>
                            ) : (
                              issue.key
                            )}
                          </td>
                          <td>{issue.type}</td>
                          <td>{issue.summary}</td>
                          <td>
                            <Tag tone={statusTone(issue.status)}>{issue.status}</Tag>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </Panel>
            </>
          )}
        </>
      )}
    </div>
  );
}
