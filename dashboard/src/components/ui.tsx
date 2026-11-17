import type { ReactNode } from "react";

export function Panel({
  title,
  action,
  children,
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

export function Kpi({
  icon,
  label,
  value,
  hint,
  tone = "ok",
}: {
  icon: string;
  label: string;
  value: string;
  hint: string;
  tone?: "ok" | "warn" | "flat";
}) {
  return (
    <div className="card">
      <div className="lab">
        <span className="ico">{icon}</span>
        {label}
      </div>
      <div className="val">{value}</div>
      <div className={`delta ${tone === "ok" ? "" : tone === "warn" ? "dn" : "flat"}`}>{hint}</div>
    </div>
  );
}

const TONES: Record<string, string> = {
  ok: "t-ok",
  warn: "t-warn",
  bad: "t-bad",
  info: "t-info",
  mut: "t-mut",
};

export function Tag({ tone = "mut", children }: { tone?: keyof typeof TONES; children: ReactNode }) {
  return <span className={`tag ${TONES[tone]}`}>{children}</span>;
}

export function statusTone(status: string): keyof typeof TONES {
  if (["completed", "published", "approved", "passed", "ok", "healthy"].includes(status)) return "ok";
  if (["awaiting_approval", "pending", "blocked", "ready"].includes(status)) return "warn";
  if (["failed", "cancelled", "error"].includes(status)) return "bad";
  if (["running", "in_progress"].includes(status)) return "info";
  return "mut";
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Loading({ rows = 3 }: { rows?: number }) {
  return (
    <div style={{ display: "grid", gap: 10, padding: "6px 0" }}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton" style={{ width: `${92 - i * 14}%` }} />
      ))}
    </div>
  );
}

export function ErrorLine({ children }: { children: ReactNode }) {
  return <div className="err">⚠ {children}</div>;
}

export function Note({ children }: { children: ReactNode }) {
  return <div className="note">ℹ {children}</div>;
}

export function Meter({ value, label, hint, tone }: { value: number; label: string; hint?: string; tone?: "warn" | "bad" }) {
  const pct = Math.max(0, Math.min(100, Math.round(value)));
  return (
    <div>
      <div className="mini">
        <span>{label}</span>
        <b>{hint ?? `${pct} %`}</b>
      </div>
      <div className={`bar ${tone ?? ""}`}>
        <i style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function ago(iso?: string): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const diff = Math.max(0, Date.now() - then);
  const m = Math.floor(diff / 60000);
  if (m < 1) return "à l'instant";
  if (m < 60) return `il y a ${m} min`;
  const h = Math.floor(m / 60);
  if (h < 24) return `il y a ${h} h`;
  return `il y a ${Math.floor(h / 24)} j`;
}
