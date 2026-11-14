import { useEffect, useMemo, useState } from "react";
import { ErrorLine, Panel, Tag, statusTone } from "./ui";
import { specs } from "../lib/specs";
import type { Catalog, Intake, Spec, SpecRow } from "../lib/specs";

const EMPTY: Intake = {
  name: "",
  objective: "",
  problem: "",
  outcomes: [],
  constraints: [],
  skills: [],
  tools: [],
  permissions: ["read"],
  kpis: [],
};

const STEPS = ["Cadrage", "Capacités", "Revue & approbation"];

function Lines({
  label,
  hint,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  hint: string;
  value: string[];
  onChange: (next: string[]) => void;
  placeholder: string;
}) {
  const [text, setText] = useState(value.join("\n"));
  return (
    <label className="field">
      <span>{label}</span>
      <textarea
        rows={3}
        placeholder={placeholder}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          onChange(
            e.target.value
              .split("\n")
              .map((l) => l.trim())
              .filter(Boolean),
          );
        }}
      />
      <small>{hint}</small>
    </label>
  );
}

function Chips({
  options,
  selected,
  onToggle,
}: {
  options: { id: string; label: string }[];
  selected: string[];
  onToggle: (id: string) => void;
}) {
  return (
    <div className="chips">
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          className={`chip ${selected.includes(o.id) ? "on" : ""}`}
          onClick={() => onToggle(o.id)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function SpecReview({ spec }: { spec: Spec }) {
  return (
    <div className="stack">
      <Panel title="Spécification" action={<Tag tone="info">v{spec.spec_version} · {spec.status}</Tag>}>
        <div className="kv">
          <span>Agent</span>
          <b>{spec.name} · {spec.slug}</b>
        </div>
        <div className="kv">
          <span>Objectif</span>
          <b className="wrap">{spec.objective}</b>
        </div>
        <div className="kv">
          <span>Problème</span>
          <b className="wrap">{spec.problem}</b>
        </div>
        <div className="kv">
          <span>Modèle</span>
          <b>{spec.llm.provider} · {spec.llm.model}</b>
        </div>
      </Panel>

      <Panel title="Architecture">
        <div className="mini">
          <span>Style</span>
          <b>{spec.architecture.style}</b>
        </div>
        <div className="components">
          {spec.architecture.components.map((c) => (
            <div key={c.name} className="row-item">
              <b>{c.name}</b>
              <span>{c.role}</span>
            </div>
          ))}
        </div>
        <div className="chips" style={{ marginTop: 10 }}>
          {spec.architecture.stack.map((s) => (
            <span key={s} className="chip">
              {s}
            </span>
          ))}
        </div>
      </Panel>

      <Panel title="Workflow" action={<Tag tone="warn">{spec.workflow.human_gates.length} portes humaines</Tag>}>
        <div className="chain">
          {spec.workflow.steps.map((s, i) => (
            <div key={s.id} className="chain-step">
              <span className={`dot ${s.gate ? "warn" : ""}`}>{i + 1}</span>
              <div>
                <b>{s.action}</b>
                <small>{s.actor}{s.gate ? " · porte humaine" : ""}</small>
              </div>
            </div>
          ))}
        </div>
      </Panel>

      <Panel
        title="Backlog proposé"
        action={<Tag tone="mut">{spec.backlog.stories.length} stories</Tag>}
      >
        <div className="epic">
          <b>{spec.backlog.epic.title}</b>
          <small>{spec.backlog.epic.summary}</small>
        </div>
        {spec.backlog.stories.map((s) => (
          <div key={s.key} className="story">
            <div className="story-head">
              <Tag tone="info">{s.key}</Tag>
              <b>{s.title}</b>
            </div>
            <small>
              En tant que {s.as_a}, je veux {s.i_want} afin de {s.so_that}.
            </small>
            <ul className="gwt">
              {s.acceptance.map((a, i) => (
                <li key={i}>
                  <b>Given</b> {a.given} · <b>When</b> {a.when} · <b>Then</b> {a.then}
                </li>
              ))}
            </ul>
            <div className="chips">
              {s.tasks.map((t) => (
                <span key={t} className="chip">
                  {t}
                </span>
              ))}
            </div>
          </div>
        ))}
      </Panel>

      <div className="grid-2">
        <Panel title="KPI">
          {spec.kpis.map((k) => (
            <div key={k.name} className="row-item">
              <b>{k.name}</b>
              <span>cible : {k.target}</span>
            </div>
          ))}
        </Panel>
        <Panel title="Risques">
          {spec.risks.map((r) => (
            <div key={r.risk} className="row-item">
              <b>{r.risk}</b>
              <span>{r.mitigation}</span>
            </div>
          ))}
        </Panel>
      </div>

      <Panel title="Plan de test">
        {spec.test_plan.map((t, i) => (
          <div key={i} className="row-item">
            <b>{t.scenario}</b>
            <span>
              Given {t.given} · When {t.when} · Then {t.then}
            </span>
          </div>
        ))}
      </Panel>
    </div>
  );
}

export function Wizard({ onClose, onApproved }: { onClose: () => void; onApproved: () => void }) {
  const [step, setStep] = useState(0);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [intake, setIntake] = useState<Intake>(EMPTY);
  const [draft, setDraft] = useState<Spec | null>(null);
  const [row, setRow] = useState<SpecRow | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    specs
      .catalog()
      .then(setCatalog)
      .catch((e) => setError(String(e.message ?? e)));
  }, []);

  const set = <K extends keyof Intake>(key: K, value: Intake[K]) => setIntake((s) => ({ ...s, [key]: value }));
  const toggle = (key: "skills" | "tools" | "permissions", id: string) =>
    setIntake((s) => ({
      ...s,
      [key]: s[key].includes(id) ? s[key].filter((v) => v !== id) : [...s[key], id],
    }));

  const hasObjective = useMemo(() => intake.objective.trim().split(/\s+/).length >= 3, [intake.objective]);
  // Step 1 needs a skill (it is chosen there); step 0 only needs a usable objective.
  const canLeaveStep = step === 0 ? hasObjective : intake.skills.length > 0;

  const generate = async () => {
    setBusy(true);
    setError("");
    try {
      setDraft(await specs.draft(intake));
      setStep(2);
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    setBusy(true);
    setError("");
    try {
      const created = row ?? (await specs.create(intake));
      setRow(created);
      const approved = await specs.approve(created.id);
      setRow(approved);
      onApproved();
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="wizard">
      <div className="wizard-head">
        <div>
          <h2>Nouvel agent</h2>
          <small>La spécification est générée localement, sans appel à un modèle, et reproductible à l'identique.</small>
        </div>
        <button className="btn ghost" onClick={onClose} type="button">
          Fermer
        </button>
      </div>

      <div className="step-rail">
        {STEPS.map((s, i) => (
          <div key={s} className={`step-pill ${i === step ? "on" : i < step ? "done" : ""}`}>
            <span>{i + 1}</span>
            {s}
          </div>
        ))}
      </div>

      {error && <ErrorLine>{error}</ErrorLine>}

      {step === 0 && (
        <Panel title="Cadrage">
          <label className="field">
            <span>Nom de l'agent</span>
            <input
              value={intake.name}
              placeholder="Radar IA"
              onChange={(e) => set("name", e.target.value)}
            />
            <small>Un identifiant technique en est dérivé (slug).</small>
          </label>
          <label className="field">
            <span>Objectif</span>
            <textarea
              rows={2}
              value={intake.objective}
              placeholder="Produire chaque lundi une veille des sorties IA en Europe"
              onChange={(e) => set("objective", e.target.value)}
            />
            <small>Trois mots minimum. C'est la phrase qui décide de l'approbation.</small>
          </label>
          <label className="field">
            <span>Problème à résoudre</span>
            <textarea
              rows={2}
              value={intake.problem}
              placeholder="La veille est faite à la main et rate des publications"
              onChange={(e) => set("problem", e.target.value)}
            />
          </label>
          <Lines
            label="Résultats attendus"
            hint="Un par ligne. Ils deviennent le « so that » des stories."
            value={intake.outcomes}
            onChange={(v) => set("outcomes", v)}
            placeholder={"un rapport le lundi matin\nles sources sont toujours citées"}
          />
          <Lines
            label="Contraintes"
            hint="Un par ligne. Aucun secret ici : les accès passent par les réglages, côté serveur."
            value={intake.constraints}
            onChange={(v) => set("constraints", v)}
            placeholder={"aucune écriture dans Jira sans approbation"}
          />
        </Panel>
      )}

      {step === 1 && (
        <div className="stack">
          <Panel title="Compétences" action={<Tag tone="mut">{intake.skills.length} sélectionnées</Tag>}>
            {catalog ? (
              <Chips options={catalog.skills} selected={intake.skills} onToggle={(id) => toggle("skills", id)} />
            ) : (
              <small>chargement…</small>
            )}
            <small style={{ display: "block", marginTop: 8 }}>
              Chaque compétence produit une story avec ses tâches et ses critères Given/When/Then.
            </small>
          </Panel>
          <Panel title="Outils">
            {catalog ? (
              <Chips options={catalog.tools} selected={intake.tools} onToggle={(id) => toggle("tools", id)} />
            ) : (
              <small>chargement…</small>
            )}
          </Panel>
          <Panel title="Permissions">
            {catalog ? (
              <Chips
                options={catalog.permissions}
                selected={intake.permissions}
                onToggle={(id) => toggle("permissions", id)}
              />
            ) : (
              <small>chargement…</small>
            )}
            <small style={{ display: "block", marginTop: 8 }}>
              « approbation humaine » est toujours ajoutée : elle ne se délègue pas à l'agent.
            </small>
          </Panel>
          <Lines
            label="KPI"
            hint="Format « nom: cible ». Sans cible, la spécification écrit « à définir » — rien n'est inventé."
            value={intake.kpis}
            onChange={(v) => set("kpis", v)}
            placeholder={"articles traités: 40 par semaine"}
          />
        </div>
      )}

      {step === 2 && draft && <SpecReview spec={draft} />}

      {step === 2 && !draft && <ErrorLine>aucune spécification générée</ErrorLine>}

      <div className="wizard-foot">
        <div>
          {row?.status === "approved" && (
            <span className="ok-line">
              ✓ Agent <b>{row.slug}</b> enregistré · approuvé par {row.approved_by} — l'agent est enregistré,
              aucun ticket Jira n'a encore été créé (Sprint 3).
            </span>
          )}
        </div>
        <div className="row-actions">
          {step > 0 && (
            <button className="btn ghost" type="button" onClick={() => setStep(step - 1)} disabled={busy}>
              Retour
            </button>
          )}
          {step < 2 && (
            <button
              className="btn"
              type="button"
              disabled={!canLeaveStep}
              onClick={() => (step === 1 ? generate() : setStep(1))}
            >
              {step === 1 ? (busy ? "Génération…" : "Générer la spécification") : "Continuer"}
            </button>
          )}
          {step === 2 && row?.status !== "approved" && (
            <button className="btn" type="button" onClick={approve} disabled={busy || !draft}>
              {busy ? "Enregistrement…" : "Approve & Create Jira Backlog"}
            </button>
          )}
          {step === 2 && row?.status === "approved" && (
            <button className="btn ghost" type="button" onClick={onClose}>
              Terminer
            </button>
          )}
        </div>
      </div>
      {step === 2 && row?.status !== "approved" && (
        <small className="foot-note">
          Le bouton enregistre l'agent et son approbation (tracée dans l'audit). La création des tickets Jira est
          livrée au Sprint 3 : rien n'est écrit dans Jira aujourd'hui.
        </small>
      )}
    </div>
  );
}

export function SpecStatus({ status }: { status: string }) {
  return <Tag tone={statusTone(status)}>{status}</Tag>;
}
