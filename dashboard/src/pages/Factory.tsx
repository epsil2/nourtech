import { useCallback, useEffect, useRef, useState } from "react";
import { Empty, ErrorLine, Kpi, Loading, Panel, Tag, ago } from "../components/ui";
import { ApiError, api } from "../lib/api";
import type { FactoryAuditItem, FactorySpec, FactoryTemplate, ProjectDetail } from "../lib/factory";
import { factory, isFailure, isProvisioning, isSettled } from "../lib/factory";
import { useAsync } from "../lib/useAsync";

/* ---------- shared helpers ---------- */

const txt = (value: unknown): string => {
  if (value === null || value === undefined) return "n/a";
  const text = String(value).trim();
  return text.length > 0 ? text : "n/a";
};

const stamp = (value?: string | null): string => (value ? ago(value) : "n/a");

const errText = (err: unknown): string => (err instanceof Error ? err.message : String(err));

type Tone = "ok" | "warn" | "bad" | "info" | "mut";

const stateTone = (state: string): Tone => {
  if (state === "READY" || state === "DEVELOPMENT_COMPLETED") return "ok";
  if (["FAILED", "BLOCKED", "CANCELLED", "REQUIRES_INTERVENTION"].includes(state)) return "bad";
  if (state === "DEVELOPMENT_RUNNING" || isProvisioning(state)) return "info";
  if (["SPECIFICATION_READY", "AWAITING_APPROVAL", "APPROVED"].includes(state)) return "warn";
  return "mut";
};

const STATE_LABELS: Record<string, string> = {
  DRAFT: "Brouillon",
  SPECIFICATION_READY: "Spécification prête",
  AWAITING_APPROVAL: "En attente d'approbation",
  APPROVED: "Approuvé",
  CREATING_JIRA: "Création Jira",
  JIRA_CREATED: "Jira créé",
  CREATING_GITHUB: "Création GitHub",
  GITHUB_CREATED: "GitHub créé",
  CONFIGURING_CICD: "Configuration CI/CD",
  REGISTERING_HERMES_WORKSPACE: "Workspace Hermes",
  VALIDATING: "Validation",
  READY: "Prêt",
  FAILED: "Échec",
  BLOCKED: "Bloqué",
  REQUIRES_INTERVENTION: "Intervention requise",
  CANCELLED: "Annulé",
  DEVELOPMENT_RUNNING: "Développement en cours",
  DEVELOPMENT_COMPLETED: "Développement terminé",
  DEVELOPMENT_FAILED: "Développement en échec",
};

const stateLabel = (state: string): string => STATE_LABELS[state] ?? state;

const opTone = (status: string): Tone => {
  if (status === "succeeded") return "ok";
  if (status === "failed") return "bad";
  if (status === "running" || status === "started") return "info";
  return "mut";
};

const ciTone = (conclusion?: string | null): Tone => {
  if (conclusion === "success") return "ok";
  if (conclusion === "failure" || conclusion === "cancelled") return "bad";
  return "mut";
};

const SPEC_SECTIONS: { key: string; label: string }[] = [
  { key: "business_requirements", label: "Exigences business" },
  { key: "functional_requirements", label: "Exigences fonctionnelles" },
  { key: "acceptance_criteria", label: "Critères d'acceptation" },
  { key: "roadmap", label: "Roadmap" },
  { key: "user_stories", label: "User stories" },
];

const sectionText = (value: unknown): string => JSON.stringify(value ?? null, null, 2);

const toSections = (content: Record<string, unknown>): Record<string, string> => {
  const out: Record<string, string> = {};
  for (const section of SPEC_SECTIONS) out[section.key] = sectionText(content[section.key]);
  return out;
};

/* ---------- page ---------- */

type View = { kind: "list" } | { kind: "wizard" } | { kind: "detail"; id: string; poll: boolean };

export function Factory() {
  const [view, setView] = useState<View>({ kind: "list" });

  return (
    <div className="stack">
      {view.kind === "list" && (
        <ListView
          onOpen={(id) => setView({ kind: "detail", id, poll: false })}
          onNew={() => setView({ kind: "wizard" })}
        />
      )}
      {view.kind === "wizard" && (
        <Wizard
          onCancel={() => setView({ kind: "list" })}
          onProvisioned={(id) => setView({ kind: "detail", id, poll: true })}
        />
      )}
      {view.kind === "detail" && (
        <DetailView
          id={view.id}
          autoPoll={view.poll}
          onBack={() => setView({ kind: "list" })}
        />
      )}
    </div>
  );
}

/* ---------- list ---------- */

function ListView({ onOpen, onNew }: { onOpen: (id: string) => void; onNew: () => void }) {
  const [list, refreshList] = useAsync(() => factory.projects());
  const [cat] = useAsync(() => factory.catalogue());

  const items = list.state === "ok" ? list.data.items : [];
  const counts = list.state === "ok" ? list.data.counts : {};
  const total = list.state === "ok" ? list.data.total : 0;
  const order =
    cat.state === "ok" && cat.data.states.length > 0 ? cat.data.states : Object.keys(counts).sort();
  const present = order.filter((state) => (counts[state] ?? 0) > 0);
  const running = order.filter((state) => isProvisioning(state)).reduce((n, state) => n + (counts[state] ?? 0), 0);
  const ready = counts.READY ?? 0;
  const failed = ["FAILED", "BLOCKED", "REQUIRES_INTERVENTION", "CANCELLED"].reduce(
    (n, state) => n + (counts[state] ?? 0),
    0,
  );

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Project Factory</h1>
          <p className="sub">
            Un projet passe par six étapes — informations, spécification, modèle technique, ressources externes,
            déploiement, approbation — avant la création réelle du projet Jira, du dépôt GitHub et du workspace Hermes.
            Aucune ressource externe n'est créée sans votre approbation.
          </p>
        </div>
        <div className="head-right">
          <button className="btn ghost" onClick={refreshList} type="button">
            Actualiser
          </button>
          <button className="btn" onClick={onNew} type="button">
            Nouveau projet
          </button>
        </div>
      </div>

      {list.state === "error" && <ErrorLine>{list.error}</ErrorLine>}
      {list.state === "loading" && <Loading rows={4} />}

      {list.state === "ok" && (
        <>
          <div className="cards">
            <Kpi icon="◈" label="Projets" value={String(total)} hint="dans la factory" />
            <Kpi icon="↻" label="Provisionnement" value={String(running)} hint="étapes en cours" tone={running ? "warn" : "flat"} />
            <Kpi icon="✓" label="Prêts" value={String(ready)} hint="état READY" tone={ready ? "ok" : "flat"} />
            <Kpi icon="✕" label="En échec" value={String(failed)} hint="failed / bloqués" tone={failed ? "warn" : "flat"} />
          </div>

          <Panel
            title="Projets de la factory"
            action={<span className="foot-note">{items.length} ligne(s) affichée(s)</span>}
          >
            {present.length > 0 && (
              <div className="chips" style={{ marginBottom: 12 }}>
                {present.map((state) => (
                  <span className="pill" key={state}>
                    {state} · {counts[state] ?? 0}
                  </span>
                ))}
              </div>
            )}

            {items.length === 0 && (
              <Empty>
                Aucun projet dans la factory.
                <div className="row-actions" style={{ justifyContent: "center", marginTop: 10 }}>
                  <button className="btn sm" onClick={onNew} type="button">
                    Créer le premier projet
                  </button>
                </div>
              </Empty>
            )}

            {items.length > 0 && (
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Clé</th>
                    <th>Nom</th>
                    <th>Type</th>
                    <th>Modèle</th>
                    <th>État</th>
                    <th>Mis à jour</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {items.map((project) => (
                    <tr key={project.id} onClick={() => onOpen(project.id)} style={{ cursor: "pointer" }}>
                      <td className="mono">{txt(project.key)}</td>
                      <td>{txt(project.name)}</td>
                      <td>{txt(project.project_type)}</td>
                      <td className="mono">{txt(project.template)}</td>
                      <td>
                        <Tag tone={stateTone(project.state)}>{txt(project.state)}</Tag>
                      </td>
                      <td>{stamp(project.updated_at)}</td>
                      <td>
                        <button
                          className="btn ghost sm"
                          onClick={(event) => {
                            event.stopPropagation();
                            onOpen(project.id);
                          }}
                          type="button"
                        >
                          Ouvrir
                        </button>
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
  );
}

/* ---------- wizard ---------- */

const STEPS = [
  "Informations",
  "Exigences",
  "Technologie",
  "Ressources externes",
  "Déploiement",
  "Revue et approbation",
];

type Draft = {
  key: string;
  name: string;
  description: string;
  business_objective: string;
  project_type: string;
  business_domain: string;
  owner: string;
  visibility: string;
  requirements: string;
  template: string;
  jira_mode: "new" | "existing";
  jira_existing_key: string;
};

const BLANK: Draft = {
  key: "",
  name: "",
  description: "",
  business_objective: "",
  project_type: "",
  business_domain: "",
  owner: "",
  visibility: "",
  requirements: "",
  template: "",
  jira_mode: "new",
  jira_existing_key: "",
};

const KEY_RE = /^[A-Z][A-Z0-9]{1,9}$/;

function StackList({ stack }: { stack: FactoryTemplate["stack"] }) {
  if (!stack) return <span className="foot-note">n/a</span>;
  if (typeof stack === "string") return <span className="foot-note">{stack}</span>;
  return (
    <div className="foot-note" style={{ display: "grid", gap: 2, marginTop: 8 }}>
      {Object.entries(stack).map(([key, value]) => (
        <span key={key}>
          <b style={{ color: "#bcb8d0", fontWeight: 550 }}>{key}</b> · {txt(value)}
        </span>
      ))}
    </div>
  );
}

function Wizard({
  onCancel,
  onProvisioned,
}: {
  onCancel: () => void;
  onProvisioned: (id: string) => void;
}) {
  const [cat] = useAsync(() => factory.catalogue());
  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<Draft>(BLANK);
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [spec, setSpec] = useState<FactorySpec | null>(null);
  const [baseContent, setBaseContent] = useState<Record<string, unknown>>({});
  const [sections, setSections] = useState<Record<string, string>>({});
  const [approved, setApproved] = useState(false);
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (cat.state !== "ok") return;
    const data = cat.data;
    setDraft((current) => ({
      ...current,
      project_type: current.project_type || data.project_types[0] || "",
      visibility: current.visibility || data.visibilities[0] || "",
      template: current.template || data.templates[0]?.key || "",
    }));
  }, [cat]);

  const set = (patch: Partial<Draft>) => setDraft((current) => ({ ...current, ...patch }));

  const run = async <T,>(action: () => Promise<T>): Promise<T | null> => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      return await action();
    } catch (err) {
      setError(errText(err));
      return null;
    } finally {
      setBusy(false);
    }
  };

  const ensureProject = async (): Promise<ProjectDetail> => {
    if (project) return project;
    if (cat.state !== "ok") {
      throw new Error("Catalogue de la factory indisponible — impossible de créer le brouillon.");
    }
    if (!draft.template || !draft.project_type || !draft.visibility) {
      throw new Error("Catalogue incomplet (type, visibilité ou modèle manquant) — rechargez la page.");
    }
    const created = await factory.create({
      key: draft.key.trim().toUpperCase(),
      name: draft.name.trim(),
      description: draft.description,
      business_objective: draft.business_objective,
      project_type: draft.project_type,
      business_domain: draft.business_domain,
      owner: draft.owner,
      visibility: draft.visibility,
      template: draft.template,
      jira_mode: draft.jira_mode,
      jira_existing_key: draft.jira_existing_key.trim().toUpperCase(),
      requirements: draft.requirements,
    });
    setProject(created.project);
    if (created.project.spec) {
      setSpec(created.project.spec);
      setBaseContent(created.project.spec.content ?? {});
      setSections(toSections(created.project.spec.content ?? {}));
    }
    return created.project;
  };

  const goNext = () =>
    run(async () => {
      if (step === 0) {
        const fields = {
          name: draft.name.trim(),
          description: draft.description,
          business_objective: draft.business_objective,
          project_type: draft.project_type,
          business_domain: draft.business_domain,
          owner: draft.owner,
          visibility: draft.visibility,
          template: draft.template,
          requirements: draft.requirements,
        };
        if (project) {
          if (project.project.key !== draft.key.trim().toUpperCase()) {
            throw new Error("La clé du projet est figée après l'enregistrement du brouillon.");
          }
          const updated = await factory.patch(project.project.id, fields);
          setProject(updated.project);
        } else {
          await ensureProject();
        }
        setStep(1);
        return;
      }
      if (step === 5) return;
      if (step === 1 && !spec) {
        setNotice("Générez la spécification avant de continuer.");
        return;
      }
      setStep(step + 1);
    });

  const generateSpec = () =>
    run(async () => {
      const current = await ensureProject();
      const result = await factory.writeSpec(current.project.id, { requirements: draft.requirements });
      setProject(result.project);
      setSpec(result.spec);
      const content = result.spec.content ?? {};
      setBaseContent(content);
      setSections(toSections(content));
      if (result.spec.version) setNotice(`Spécification v${result.spec.version} générée (${txt(result.spec.status)}).`);
    });

  const saveSpec = () =>
    run(async () => {
      const current = await ensureProject();
      const content: Record<string, unknown> = { ...baseContent };
      for (const section of SPEC_SECTIONS) {
        const raw = sections[section.key] ?? "";
        try {
          content[section.key] = JSON.parse(raw);
        } catch (err) {
          throw new Error(`Section « ${section.label} » : JSON invalide — ${errText(err)}`);
        }
      }
      const result = await factory.writeSpec(current.project.id, { requirements: draft.requirements, content });
      setProject(result.project);
      setSpec(result.spec);
      setBaseContent(result.spec.content ?? content);
      setSections(toSections(result.spec.content ?? content));
      setNotice(`Spécification v${result.spec.version} enregistrée (${txt(result.spec.status)}).`);
    });

  const chooseTemplate = (key: string) =>
    run(async () => {
      set({ template: key });
      const current = await ensureProject();
      const result = await factory.patch(current.project.id, { template: key });
      setProject(result.project);
      setNotice(`Modèle « ${key} » retenu pour le brouillon.`);
    });

  const chooseVisibility = (value: string) =>
    run(async () => {
      set({ visibility: value });
      if (!project) return;
      const result = await factory.patch(project.project.id, { visibility: value });
      setProject(result.project);
      setNotice(`Visibilité « ${value} » enregistrée sur le brouillon.`);
    });

  const approveAndProvision = () =>
    run(async () => {
      const current = await ensureProject();
      const id = current.project.id;
      if (spec) await factory.submitSpec(id);
      const parts: string[] = [];
      parts.push(
        draft.jira_mode === "existing"
          ? `Jira existant: ${txt(draft.jira_existing_key.trim().toUpperCase())}`
          : "Jira: création d'un nouveau projet",
      );
      parts.push(`Visibilité GitHub: ${txt(draft.visibility)}`);
      if (notes.trim()) parts.push(notes.trim());
      await factory.approve(id, parts.join(" · "));
      const provisioned = await factory.provision(id);
      setProject(provisioned.project);
      setNotice("Approbation enregistrée, provisionnement lancé.");
      onProvisioned(id);
    });

  const linkGithub = () =>
    run(async () => {
      const started = await api.post<{ authorize_url?: string }>("/api/v1/factory/github/oauth/start", {});
      const url = started?.authorize_url;
      if (!url) throw new ApiError(500, "GitHub n'a pas fourni d'URL d'autorisation.");
      setNotice("Redirection vers GitHub pour autoriser la App…");
      window.location.assign(url);
    });

  const templates = cat.state === "ok" ? cat.data.templates : [];
  const projectTypes = cat.state === "ok" ? cat.data.project_types : [];
  const visibilities = cat.state === "ok" ? cat.data.visibilities : [];
  const jira = cat.state === "ok" ? cat.data.jira : null;
  const github = cat.state === "ok" ? cat.data.github : null;

  const keyOk = KEY_RE.test(draft.key.trim().toUpperCase());
  const catalogueOk = cat.state === "ok" && projectTypes.length > 0 && templates.length > 0 && visibilities.length > 0;
  const canNext =
    step === 0
      ? catalogueOk && keyOk && draft.name.trim().length >= 2
      : step === 1
        ? spec !== null
        : step === 2
          ? draft.template.length > 0
          : step === 3
            ? draft.jira_mode === "new" || draft.jira_existing_key.trim().length >= 2
            : step === 4
              ? true
              : false;

  const specNote = typeof baseContent.note === "string" ? baseContent.note : null;
  const specGenerator = typeof baseContent.generator === "string" ? baseContent.generator : null;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Nouveau projet</h1>
          <p className="sub">
            Six étapes, un brouillon traçable. Le brouillon est enregistré côté API dès la fin de l'étape 1 ; rien n'est
            créé dans Jira ni dans GitHub avant l'approbation de l'étape 6.
          </p>
        </div>
        <div className="head-right">
          <button className="btn ghost" onClick={onCancel} type="button">
            Annuler
          </button>
        </div>
      </div>

      {error && <ErrorLine>{error}</ErrorLine>}
      {notice && <div className="tag t-info">{notice}</div>}
      {cat.state === "error" && <ErrorLine>{cat.error}</ErrorLine>}

      <Panel
        title={`Étape ${step + 1} / 6 — ${STEPS[step]}`}
        action={
          <span className="foot-note">
            {project ? `projet ${txt(project.project.key)} · ${stateLabel(txt(project.state))}` : "brouillon non enregistré"}
          </span>
        }
      >
        <div className="step-rail" style={{ marginBottom: 16 }}>
          {STEPS.map((label, index) => (
            <div
              key={label}
              className={`step-pill ${index === step ? "on" : index < step ? "done" : ""}`}
            >
              <span>{index + 1}</span>
              {label}
            </div>
          ))}
        </div>

        {step === 0 && (
          <>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0 14px" }}>
              <div className="field">
                <span>Nom du projet</span>
                <input
                  value={draft.name}
                  maxLength={80}
                  onChange={(event) => set({ name: event.target.value })}
                  placeholder="Veille IA hebdomadaire"
                />
              </div>
              <div className="field">
                <span>Clé (2-10 caractères, majuscules)</span>
                <input
                  value={draft.key}
                  maxLength={10}
                  disabled={project !== null}
                  onChange={(event) =>
                    set({ key: event.target.value.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 10) })
                  }
                  placeholder="VEILLE"
                />
                <small className="mono">
                  {project ? "clé figée après enregistrement du brouillon" : draft.key ? draft.key : "A-Z puis A-Z 0-9"}
                </small>
              </div>
              <div className="field" style={{ gridColumn: "1 / -1" }}>
                <span>Description</span>
                <textarea
                  rows={3}
                  value={draft.description}
                  onChange={(event) => set({ description: event.target.value })}
                  placeholder="Ce que fait le produit, pour qui, et ce qui est hors périmètre."
                />
              </div>
              <div className="field" style={{ gridColumn: "1 / -1" }}>
                <span>Objectif business</span>
                <textarea
                  rows={2}
                  value={draft.business_objective}
                  onChange={(event) => set({ business_objective: event.target.value })}
                  placeholder="Le résultat attendu côté métier, avec son échéance."
                />
              </div>
              <div className="field">
                <span>Type de projet</span>
                <select
                  className="inp"
                  value={draft.project_type}
                  onChange={(event) => set({ project_type: event.target.value })}
                >
                  {projectTypes.length === 0 && <option value="">n/a</option>}
                  {projectTypes.map((type) => (
                    <option key={type} value={type}>
                      {type}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <span>Domaine métier</span>
                <input
                  value={draft.business_domain}
                  onChange={(event) => set({ business_domain: event.target.value })}
                  placeholder="Finance, RH, supply chain…"
                />
              </div>
              <div className="field">
                <span>Propriétaire</span>
                <input
                  value={draft.owner}
                  onChange={(event) => set({ owner: event.target.value })}
                  placeholder="compte propriétaire du projet"
                />
              </div>
              <div className="field">
                <span>Visibilité</span>
                <div className="chips">
                  {visibilities.length === 0 && <span className="pill">n/a</span>}
                  {visibilities.map((value) => (
                    <button
                      key={value}
                      className={`chip ${draft.visibility === value ? "on" : ""}`}
                      onClick={() => set({ visibility: value })}
                      type="button"
                    >
                      {value}
                    </button>
                  ))}
                </div>
              </div>
            </div>
            <p className="foot-note">
              « Suivant » enregistre le brouillon (état DRAFT). Aucune ressource externe n'est créée à cette étape.
            </p>
          </>
        )}

        {step === 1 && (
          <>
            <div className="field">
              <span>Exigences en langage naturel</span>
              <textarea
                rows={6}
                value={draft.requirements}
                onChange={(event) => set({ requirements: event.target.value })}
                placeholder="Décrivez les parcours, les règles métier, les contraintes. Une exigence par ligne."
              />
            </div>
            <div className="row-actions" style={{ marginBottom: 8 }}>
              <button
                className="btn"
                onClick={generateSpec}
                disabled={busy || draft.requirements.trim().length < 10}
                type="button"
              >
                Générer la spécification
              </button>
              {spec && (
                <span className="pill">
                  v{spec.version} · {txt(spec.status)}
                </span>
              )}
            </div>

            {!spec && (
              <Empty>
                Aucune spécification pour ce brouillon. La génération produit un brouillon structuré à partir de votre
                saisie, sans modèle de langage : chaque section reste éditable avant approbation.
              </Empty>
            )}

            {spec && (
              <>
                {specNote && <p className="sub">{specNote}</p>}
                {specGenerator && (
                  <p className="foot-note">
                    générateur <span className="mono">{specGenerator}</span>
                  </p>
                )}
                {SPEC_SECTIONS.map((section) => (
                  <div className="field" key={section.key}>
                    <span>
                      {section.label} <span className="mono">{section.key}</span>
                    </span>
                    <textarea
                      rows={7}
                      className="mono"
                      style={{ fontFamily: "ui-monospace, Menlo, monospace", fontSize: 11.5 }}
                      value={sections[section.key] ?? ""}
                      onChange={(event) =>
                        setSections((current) => ({ ...current, [section.key]: event.target.value }))
                      }
                    />
                  </div>
                ))}
                <button className="btn ghost" onClick={saveSpec} disabled={busy} type="button">
                  Enregistrer la spécification
                </button>
              </>
            )}
          </>
        )}

        {step === 2 && (
          <>
            {templates.length === 0 && <Empty>Aucun modèle technique dans le catalogue.</Empty>}
            <div
              className="cards"
              style={{ gridTemplateColumns: "repeat(auto-fit, minmax(230px, 1fr))", marginTop: 0 }}
            >
              {templates.map((template) => {
                const selected = draft.template === template.key;
                return (
                  <label
                    key={template.key}
                    className="card"
                    style={{
                      cursor: "pointer",
                      borderColor: selected ? "rgba(124, 92, 255, 0.65)" : undefined,
                      boxShadow: selected ? "inset 0 0 0 1px rgba(124, 92, 255, 0.45)" : undefined,
                    }}
                  >
                    <div className="lab">
                      <input
                        type="radio"
                        name="factory-template"
                        checked={selected}
                        onChange={() => chooseTemplate(template.key)}
                      />
                      {template.name}
                    </div>
                    <div className="val" style={{ fontSize: 13 }}>
                      {txt(template.key)} · v{txt(template.version)}
                    </div>
                    <p className="foot-note" style={{ marginTop: 6 }}>
                      {txt(template.description)}
                    </p>
                    <StackList stack={template.stack} />
                  </label>
                );
              })}
            </div>
            <p className="foot-note">
              Le modèle est appliqué au dépôt GitHub à l'étape « appliquer le modèle » du provisionnement.
            </p>
          </>
        )}

        {step === 3 && (
          <>
            {jira && !jira.ready && (
              <ErrorLine>
                Jira n'est pas configuré sur cet hôte — {txt(jira.detail)}. Le provisionnement échouera à l'étape
                « créer le projet Jira ».
              </ErrorLine>
            )}
            {github && !github.ready && (
              <ErrorLine>
                GitHub n'est pas configuré sur cet hôte — {txt(github.detail)}. Le provisionnement échouera à l'étape
                « créer le dépôt ».
              </ErrorLine>
            )}

            <div className="field">
              <span>Projet Jira</span>
              <label className="sub">
                <input
                  type="radio"
                  name="jira-mode"
                  checked={draft.jira_mode === "new"}
                  onChange={() => set({ jira_mode: "new" })}
                />{" "}
                Créer un nouveau projet Jira
              </label>
              <label className="sub">
                <input
                  type="radio"
                  name="jira-mode"
                  checked={draft.jira_mode === "existing"}
                  onChange={() => set({ jira_mode: "existing" })}
                />{" "}
                Utiliser un projet Jira existant
              </label>
              {draft.jira_mode === "existing" && (
                <input
                  value={draft.jira_existing_key}
                  maxLength={10}
                  onChange={(event) =>
                    set({
                      jira_existing_key: event.target.value.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 10),
                    })
                  }
                  placeholder="SCRUM"
                />
              )}
              <small>
                site Jira : <span className="mono">{txt(jira?.site)}</span> ·{" "}
                {jira?.ready ? "prêt" : "non configuré"}
              </small>
            </div>

            <div className="field">
              <span>GitHub</span>
              <small>
                propriétaire : <span className="mono">{txt(github?.owner)}</span> ·{" "}
                {github?.ready ? "prêt" : "non configuré"}
              </small>
              {github && !github.ready && (
                <div className="row" style={{ gap: 8, alignItems: "center" }}>
                  <button className="btn ghost sm" type="button" onClick={linkGithub} disabled={busy}>
                    Lier la GitHub App
                  </button>
                  <small className="foot-note">
                    Ouvre l'autorisation GitHub ; l'installation trouvée est enregistrée côté serveur.
                  </small>
                </div>
              )}
              <span>Visibilité du dépôt</span>
              <div className="chips">
                {visibilities.length === 0 && <span className="pill">n/a</span>}
                {visibilities.map((value) => (
                  <button
                    key={value}
                    className={`chip ${draft.visibility === value ? "on" : ""}`}
                    onClick={() => chooseVisibility(value)}
                    disabled={busy}
                    type="button"
                  >
                    {value}
                  </button>
                ))}
              </div>
            </div>

            <p className="foot-note">
              Le mode Jira est transmis à la création du brouillon ; le choix courant est consigné dans les notes
              d'approbation et dans la piste d'audit.
            </p>
          </>
        )}

        {step === 4 && (
          <div>
            <div className="kv">
              <span>Hôte</span>
              <b className="wrap">VPS existant — aucun serveur supplémentaire n'est provisionné</b>
            </div>
            <div className="kv">
              <span>Orchestration</span>
              <b className="wrap">Docker Compose, un service par conteneur</b>
            </div>
            <div className="kv">
              <span>Routage</span>
              <b className="wrap">Traefik existant, règles ajoutées par labels</b>
            </div>
            <div className="kv">
              <span>Environnement</span>
              <b className="wrap">Environnement de test non-production</b>
            </div>
            <div className="kv">
              <span>Production</span>
              <b className="wrap">Aucun déploiement production automatique — approbation humaine séparée</b>
            </div>
            <p className="foot-note">
              Ces règles sont appliquées par le modèle technique et le pipeline CI ; elles ne sont pas modifiables ici.
            </p>
          </div>
        )}

        {step === 5 && (
          <>
            <div className="kv">
              <span>Nom</span>
              <b className="wrap">{txt(draft.name)}</b>
            </div>
            <div className="kv">
              <span>Clé</span>
              <b className="wrap mono">{txt(draft.key)}</b>
            </div>
            <div className="kv">
              <span>Type</span>
              <b className="wrap">{txt(draft.project_type)}</b>
            </div>
            <div className="kv">
              <span>Domaine</span>
              <b className="wrap">{txt(draft.business_domain)}</b>
            </div>
            <div className="kv">
              <span>Propriétaire</span>
              <b className="wrap">{txt(draft.owner)}</b>
            </div>
            <div className="kv">
              <span>Visibilité</span>
              <b className="wrap">{txt(draft.visibility)}</b>
            </div>
            <div className="kv">
              <span>Modèle</span>
              <b className="wrap mono">{txt(draft.template)}</b>
            </div>
            <div className="kv">
              <span>Mode Jira</span>
              <b className="wrap">
                {draft.jira_mode === "existing"
                  ? `projet existant ${txt(draft.jira_existing_key)}`
                  : "créer un nouveau projet Jira"}
              </b>
            </div>
            <div className="kv">
              <span>Spécification</span>
              <b className="wrap">
                {spec ? `v${spec.version} · ${txt(spec.status)}` : "n/a"}
              </b>
            </div>
            <div className="kv">
              <span>Objectif business</span>
              <b className="wrap">{txt(draft.business_objective)}</b>
            </div>
            <div className="kv">
              <span>Description</span>
              <b className="wrap">{txt(draft.description)}</b>
            </div>
            <div className="kv">
              <span>Exigences</span>
              <b className="wrap" style={{ whiteSpace: "pre-wrap", textAlign: "right" }}>
                {txt(draft.requirements)}
              </b>
            </div>
            <div className="kv">
              <span>Sections de spécification</span>
              <b className="wrap">{SPEC_SECTIONS.filter((section) => sections[section.key]).length} / 5</b>
            </div>

            <div className="field" style={{ marginTop: 10 }}>
              <span>Notes d'approbation (optionnel)</span>
              <textarea rows={2} value={notes} onChange={(event) => setNotes(event.target.value)} />
            </div>

            <label className="sub" style={{ display: "block", marginTop: 6 }}>
              <input type="checkbox" checked={approved} onChange={(event) => setApproved(event.target.checked)} />{" "}
              J'approuve la création des ressources externes
            </label>

            <div className="row-actions" style={{ marginTop: 12 }}>
              <button
                className="btn"
                onClick={approveAndProvision}
                disabled={busy || !approved || spec === null}
                type="button"
              >
                Approuver et provisionner
              </button>
              {!approved && <span className="foot-note">cochez l'approbation pour activer l'action</span>}
              {approved && spec === null && (
                <span className="foot-note">générez la spécification à l'étape 2</span>
              )}
            </div>
          </>
        )}
      </Panel>

      <div className="wizard-foot">
        <button className="btn ghost" onClick={() => setStep(Math.max(0, step - 1))} disabled={busy || step === 0} type="button">
          Précédent
        </button>
        <span className="foot-note">
          étape {step + 1} sur 6 · {STEPS[step]}
        </span>
        {step < 5 && (
          <button className="btn" onClick={goNext} disabled={busy || !canNext} type="button">
            Suivant
          </button>
        )}
      </div>
    </>
  );
}

/* ---------- detail ---------- */

function DetailView({ id, autoPoll, onBack }: { id: string; autoPoll: boolean; onBack: () => void }) {
  const [cat] = useAsync(() => factory.catalogue());
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [audit, setAudit] = useState<FactoryAuditItem[]>([]);
  const [auditError, setAuditError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [polling, setPolling] = useState(autoPoll);
  const pollFor = useRef<"none" | "backlog" | "execution">("none");
  const [issueKey, setIssueKey] = useState("");
  const [runtime, setRuntime] = useState("45m");

  const load = useCallback(
    async (quiet: boolean): Promise<ProjectDetail | null> => {
      if (!quiet) setLoading(true);
      try {
        const fresh = await factory.project(id);
        setDetail(fresh);
        setError(null);
        return fresh;
      } catch (err) {
        setError(errText(err));
        return null;
      } finally {
        if (!quiet) setLoading(false);
      }
    },
    [id],
  );

  const loadAudit = useCallback(async () => {
    try {
      const result = await factory.audit(id);
      setAudit(result.items);
      setAuditError(null);
    } catch (err) {
      setAuditError(errText(err));
    }
  }, [id]);

  useEffect(() => {
    void load(false);
    void loadAudit();
  }, [load, loadAudit]);

  useEffect(() => {
    if (detail && isProvisioning(detail.state)) {
      pollFor.current = "none";
      setPolling(true);
    }
  }, [detail]);

  useEffect(() => {
    if (!polling) return;
    const deadline = Date.now() + 5 * 60 * 1000;
    const timer = setInterval(() => {
      if (Date.now() > deadline) {
        setPolling(false);
        return;
      }
      void load(true).then((fresh) => {
        if (!fresh) return;
        const want = pollFor.current;
        const done =
          isFailure(fresh.state) ||
          (want === "backlog"
            ? (fresh.jira_issues ?? []).length > 0
            : want === "execution"
              ? (fresh.executions ?? []).length > 0
              : isSettled(fresh.state));
        if (done) {
          pollFor.current = "none";
          setPolling(false);
          void loadAudit();
        }
      });
    }, 3000);
    return () => clearInterval(timer);
  }, [polling, load, loadAudit]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  };

  const steps = cat.state === "ok" ? cat.data.steps : [];
  const stepLabel = (key: string): string => steps.find((step) => step.key === key)?.label ?? key;

  const issues = detail?.jira_issues ?? [];
  const operations = detail?.operations ?? [];
  const executions = detail?.executions ?? [];
  const approvals = detail?.approvals ?? [];
  const ciRuns = detail?.ci_runs ?? [];

  const launchBacklog = () =>
    run(async () => {
      const result = await factory.backlog(id);
      setNotice(`Création du backlog Jira lancée — job ${result.job.id.slice(0, 8)}.`);
      pollFor.current = "backlog";
      setPolling(true);
      await load(true);
      await loadAudit();
    });

  const retryStep = () =>
    run(async () => {
      const result = await factory.retry(id);
      setNotice(`Étape « ${stepLabel(result.step)} » relancée — job ${result.job.id.slice(0, 8)}.`);
      pollFor.current = "none";
      setPolling(true);
      await load(true);
      await loadAudit();
    });

  const launchDevelopment = () =>
    run(async () => {
      const key = issueKey.trim().toUpperCase();
      if (!key) throw new Error("Saisissez ou sélectionnez une clé d'issue Jira.");
      const result = await factory.development(id, {
        issue_key: key,
        runtime_limit: runtime.trim() || "45m",
        retry_limit: 1,
      });
      setNotice(
        `Tâche Hermes ${txt(result.execution.task_id)} lancée sur le board ${txt(result.execution.board)} pour ${txt(
          result.execution.issue_key ?? key,
        )}.`,
      );
      pollFor.current = "execution";
      setPolling(true);
      await load(true);
      await loadAudit();
    });

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{detail ? `${txt(detail.project.key)} — ${txt(detail.project.name)}` : "Projet"}</h1>
          <p className="sub">
            Spécification, approbations, provisionnement Jira / GitHub / workspace Hermes, exécutions et piste d'audit.
          </p>
        </div>
        <div className="head-right">
          <button className="btn ghost" onClick={onBack} type="button">
            Liste
          </button>
          <button
            className="btn ghost"
            onClick={() => {
              void load(false);
              void loadAudit();
            }}
            disabled={busy}
            type="button"
          >
            Actualiser
          </button>
          {detail?.jira && issues.length === 0 && (
            <button className="btn" onClick={launchBacklog} disabled={busy} type="button">
              Créer le backlog Jira
            </button>
          )}
          {detail && isFailure(detail.state) && (
            <button className="btn" onClick={retryStep} disabled={busy} type="button">
              Relancer l'étape en échec
            </button>
          )}
        </div>
      </div>

      {error && <ErrorLine>{error}</ErrorLine>}
      {notice && <div className="tag t-info">{notice}</div>}
      {polling && <p className="foot-note">actualisation en cours — toutes les 3 secondes</p>}

      {loading && !detail && <Loading rows={4} />}

      {detail && (
        <>
          <div className="cards">
            <Kpi icon="◈" label="État" value={stateLabel(detail.state)} hint={txt(detail.state)} tone={stateTone(detail.state) === "bad" ? "warn" : "flat"} />
            <Kpi icon="→" label="Étape suivante" value={detail.next_step ? stepLabel(detail.next_step) : "aucune"} hint={txt(detail.next_step)} tone="flat" />
            <Kpi icon="◱" label="Tickets Jira" value={String(issues.length)} hint="issues rattachées" tone="flat" />
            <Kpi icon="▶" label="Exécutions" value={String(executions.length)} hint="tâches Hermes" tone="flat" />
          </div>

          <Panel
            title="Informations projet"
            action={<Tag tone={stateTone(detail.state)}>{txt(detail.state)}</Tag>}
          >
            <div className="kv">
              <span>Type</span>
              <b className="wrap">{txt(detail.project.project_type)}</b>
            </div>
            <div className="kv">
              <span>Domaine</span>
              <b className="wrap">{txt(detail.project.business_domain)}</b>
            </div>
            <div className="kv">
              <span>Propriétaire</span>
              <b className="wrap">{txt(detail.project.owner)}</b>
            </div>
            <div className="kv">
              <span>Visibilité</span>
              <b className="wrap">{txt(detail.project.visibility)}</b>
            </div>
            <div className="kv">
              <span>Modèle</span>
              <b className="wrap mono">{txt(detail.project.template)}</b>
            </div>
            <div className="kv">
              <span>Créé par</span>
              <b className="wrap">
                {txt(detail.project.created_by)} · {stamp(detail.project.created_at)}
              </b>
            </div>
            <div className="kv">
              <span>Objectif business</span>
              <b className="wrap">{txt(detail.project.business_objective)}</b>
            </div>
            <div className="kv">
              <span>Description</span>
              <b className="wrap">{txt(detail.project.description)}</b>
            </div>
            <div className="kv">
              <span>Étape en échec</span>
              <b className="wrap">{detail.provisioning?.failed_step ? stepLabel(detail.provisioning.failed_step) : "n/a"}</b>
            </div>
            <div className="kv">
              <span>Étape suivante</span>
              <b className="wrap">{detail.next_step ? stepLabel(detail.next_step) : "n/a"}</b>
            </div>
            {detail.provisioning?.last_error && <ErrorLine>{detail.provisioning.last_error}</ErrorLine>}
            {detail.project.requirements && (
              <div className="field">
                <span>Exigences saisies</span>
                <p className="sub" style={{ whiteSpace: "pre-wrap" }}>
                  {detail.project.requirements}
                </p>
              </div>
            )}
          </Panel>

          <div className="grid-2">
            <Panel title="Opérations de provisionnement">
              {operations.length === 0 && <Empty>Aucune opération enregistrée.</Empty>}
              {operations.length > 0 && (
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>Étape</th>
                      <th>Statut</th>
                      <th>Essai</th>
                      <th>Démarrée</th>
                      <th>Terminée</th>
                      <th>Référence</th>
                      <th>Erreur</th>
                    </tr>
                  </thead>
                  <tbody>
                    {operations.map((operation, index) => (
                      <tr key={`${operation.step}-${operation.attempt ?? index}`}>
                        <td>
                          {stepLabel(operation.step)}
                          <div className="mono">{operation.step}</div>
                        </td>
                        <td>
                          <Tag tone={opTone(operation.status)}>{txt(operation.status)}</Tag>
                        </td>
                        <td>{operation.attempt ?? "n/a"}</td>
                        <td>{stamp(operation.started_at)}</td>
                        <td>{stamp(operation.completed_at)}</td>
                        <td className="mono">{txt(operation.external_id)}</td>
                        <td>{operation.error ? <span style={{ color: "var(--bad)" }}>{operation.error}</span> : "n/a"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Panel>

            <Panel title="Spécification et approbations">
              <div className="kv">
                <span>Version</span>
                <b>{detail.spec ? `v${detail.spec.version}` : "n/a"}</b>
              </div>
              <div className="kv">
                <span>Statut</span>
                <b>{detail.spec ? txt(detail.spec.status) : "n/a"}</b>
              </div>
              <div className="kv">
                <span>Sections</span>
                <b>
                  {detail.spec
                    ? `${SPEC_SECTIONS.filter((section) => (detail.spec?.content ?? {})[section.key] !== undefined).length} / ${SPEC_SECTIONS.length}`
                    : "n/a"}
                </b>
              </div>
              <div style={{ marginTop: 10 }}>
                {approvals.length === 0 && <Empty>Aucune approbation enregistrée.</Empty>}
                {approvals.map((approval, index) => (
                  <div className="row-item" key={`${approval.actor}-${approval.created_at ?? index}`}>
                    <span className="t">
                      <b>{txt(approval.actor)}</b>
                      <span>{txt(approval.notes)}</span>
                    </span>
                    <Tag tone={approval.decision === "approved" ? "ok" : "mut"}>{txt(approval.decision)}</Tag>
                    <span className="foot-note">{stamp(approval.created_at)}</span>
                  </div>
                ))}
              </div>
            </Panel>
          </div>

          <div className="grid-2">
            <Panel
              title="Jira"
              action={
                detail.jira?.url ? (
                  <a className="link" href={txt(detail.jira.url)} target="_blank" rel="noreferrer">
                    ouvrir Jira
                  </a>
                ) : undefined
              }
            >
              {!detail.jira && <Empty>Aucun projet Jira provisionné.</Empty>}
              {detail.jira && (
                <>
                  <div className="kv">
                    <span>Clé</span>
                    <b className="mono">{txt(detail.jira.jira_key)}</b>
                  </div>
                  <div className="kv">
                    <span>URL</span>
                    <b className="wrap">
                      {detail.jira.url ? (
                        <a href={txt(detail.jira.url)} target="_blank" rel="noreferrer">
                          {txt(detail.jira.url)}
                        </a>
                      ) : (
                        "n/a"
                      )}
                    </b>
                  </div>
                  <div className="kv">
                    <span>Créé par la factory</span>
                    <b>{detail.jira.created === null || detail.jira.created === undefined ? "n/a" : detail.jira.created ? "oui" : "non"}</b>
                  </div>
                  <div style={{ marginTop: 10 }}>
                    {issues.length === 0 && <Empty>Aucune issue rattachée — le backlog peut être créé.</Empty>}
                    {issues.length > 0 && (
                      <table className="tbl">
                        <thead>
                          <tr>
                            <th>Clé</th>
                            <th>Type</th>
                            <th>Résumé</th>
                            <th>Epic</th>
                          </tr>
                        </thead>
                        <tbody>
                          {issues.map((issue) => (
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
                              <td>{txt(issue.issue_type)}</td>
                              <td>{txt(issue.summary)}</td>
                              <td className="mono">{txt(issue.epic_key)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                </>
              )}
            </Panel>

            <div className="stack">
              <Panel title="Dépôt GitHub">
                {!detail.repository && <Empty>Aucun dépôt créé.</Empty>}
                {detail.repository && (
                  <>
                    <div className="kv">
                      <span>Dépôt</span>
                      <b className="wrap mono">
                        {detail.repository.url ? (
                          <a href={txt(detail.repository.url)} target="_blank" rel="noreferrer">
                            {txt(detail.repository.full_name)}
                          </a>
                        ) : (
                          txt(detail.repository.full_name)
                        )}
                      </b>
                    </div>
                    <div className="kv">
                      <span>Branche par défaut</span>
                      <b className="mono">{txt(detail.repository.default_branch)}</b>
                    </div>
                    <div className="kv">
                      <span>Privé</span>
                      <b>{detail.repository.private === null || detail.repository.private === undefined ? "n/a" : detail.repository.private ? "oui" : "non"}</b>
                    </div>
                    <div className="kv">
                      <span>Commit du modèle</span>
                      <b className="mono">{txt(detail.repository.template_commit)}</b>
                    </div>
                  </>
                )}
              </Panel>

              <Panel title="Workspace Hermes">
                {!detail.workspace && <Empty>Aucun workspace enregistré.</Empty>}
                {detail.workspace && (
                  <>
                    <div className="kv">
                      <span>Board</span>
                      <b className="mono">{txt(detail.workspace.board)}</b>
                    </div>
                    <div className="kv">
                      <span>Chemin</span>
                      <b className="wrap mono">{txt(detail.workspace.workspace_path)}</b>
                    </div>
                  </>
                )}
              </Panel>
            </div>
          </div>

          <Panel title="Runs CI">
            {ciRuns.length === 0 && <Empty>Aucun run CI remonté par GitHub.</Empty>}
            {ciRuns.length > 0 && (
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Nom</th>
                    <th>Statut</th>
                    <th>Conclusion</th>
                    <th>Commit</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {ciRuns.map((ciRun, index) => (
                    <tr key={`${ciRun.head_sha ?? "run"}-${index}`}>
                      <td>{txt(ciRun.name)}</td>
                      <td>{txt(ciRun.status)}</td>
                      <td>
                        <Tag tone={ciTone(ciRun.conclusion)}>{txt(ciRun.conclusion)}</Tag>
                      </td>
                      <td className="mono">{ciRun.head_sha ? ciRun.head_sha.slice(0, 7) : "n/a"}</td>
                      <td>
                        {ciRun.url ? (
                          <a className="link" href={ciRun.url} target="_blank" rel="noreferrer">
                            logs
                          </a>
                        ) : (
                          "n/a"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>

          <Panel title="Exécutions Hermes">
            {executions.length === 0 && <Empty>Aucune tâche Hermes lancée.</Empty>}
            {executions.length > 0 && (
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Issue</th>
                    <th>Tâche</th>
                    <th>Board</th>
                    <th>Statut</th>
                    <th>Lancée</th>
                  </tr>
                </thead>
                <tbody>
                  {executions.map((execution, index) => (
                    <tr key={`${execution.task_id ?? "task"}-${index}`}>
                      <td className="mono">{txt(execution.issue_key)}</td>
                      <td className="mono">{txt(execution.task_id)}</td>
                      <td className="mono">{txt(execution.board)}</td>
                      <td>
                        <Tag tone={opTone(execution.status)}>{txt(execution.status)}</Tag>
                      </td>
                      <td>{stamp(execution.launched_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>

          {detail.state === "READY" && (
            <Panel title="Lancer la tâche Hermes">
              <div style={{ display: "grid", gridTemplateColumns: "1fr 140px", gap: "0 14px" }}>
                <div className="field">
                  <span>Issue Jira</span>
                  <select className="inp" value={issueKey} onChange={(event) => setIssueKey(event.target.value)}>
                    <option value="">— saisir une clé —</option>
                    {issues.map((issue) => (
                      <option key={issue.key} value={issue.key}>
                        {issue.key} · {txt(issue.summary)}
                      </option>
                    ))}
                  </select>
                  <input
                    value={issueKey}
                    onChange={(event) => setIssueKey(event.target.value.toUpperCase())}
                    placeholder="SCRUM-123"
                  />
                </div>
                <div className="field">
                  <span>Durée maximale</span>
                  <input value={runtime} onChange={(event) => setRuntime(event.target.value)} placeholder="45m" />
                </div>
              </div>
              <p className="foot-note">
                La tâche est créée sur le board du workspace avec une clé d'idempotence, une durée maximale et un seul
                nouvel essai.
              </p>
              <button
                className="btn"
                onClick={launchDevelopment}
                disabled={busy || issueKey.trim().length < 2}
                type="button"
              >
                Lancer la tâche Hermes
              </button>
            </Panel>
          )}

          <Panel
            title="Piste d'audit"
            action={
              <button className="btn ghost sm" onClick={() => void loadAudit()} disabled={busy} type="button">
                Recharger
              </button>
            }
          >
            {auditError && <ErrorLine>{auditError}</ErrorLine>}
            {!auditError && audit.length === 0 && <Empty>Aucun événement d'audit pour ce projet.</Empty>}
            {audit.length > 0 && (
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Quand</th>
                    <th>Action</th>
                    <th>Acteur</th>
                    <th>Entité</th>
                    <th>Payload</th>
                  </tr>
                </thead>
                <tbody>
                  {audit.map((item, index) => (
                    <tr key={`${item.action}-${index}`}>
                      <td>{stamp(item.created_at ?? item.at)}</td>
                      <td className="mono">{txt(item.action)}</td>
                      <td>{txt(item.actor)}</td>
                      <td className="mono">{txt(item.entity_type)}</td>
                      <td className="mono">{item.payload ? JSON.stringify(item.payload).slice(0, 140) : "n/a"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>
        </>
      )}
    </>
  );
}
