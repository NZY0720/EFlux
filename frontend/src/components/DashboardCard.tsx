import type React from "react";

type Tone = "accent" | "amber" | "success" | "danger" | "violet" | "muted";
type TitleIcon = React.ElementType<{ size?: number | string; className?: string }>;

const accentClass = {
  accent: "text-[var(--accent)]",
  amber: "text-[var(--warning)]",
  success: "text-[var(--success)]",
  danger: "text-[var(--danger)]",
  violet: "text-[var(--violet)]",
  muted: "text-[var(--text-subtle)]",
};

export function CardTitle({
  icon: Icon,
  children,
  accent = "accent",
  action,
}: {
  icon: TitleIcon;
  children: React.ReactNode;
  accent?: keyof typeof accentClass;
  action?: React.ReactNode;
}) {
  return (
    <div className="mb-3 flex items-center justify-between gap-3">
      <h3 className="flex min-w-0 items-center gap-2 text-sm font-semibold uppercase tracking-wide text-[var(--text-muted)]">
        <Icon size={16} className={accentClass[accent]} />
        <span className="truncate">{children}</span>
      </h3>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

export function DashboardCard({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return <section className={`eflux-card p-4 ${className}`}>{children}</section>;
}

export function EmptyState({
  icon: Icon,
  title,
  body,
  className = "",
}: {
  icon?: TitleIcon;
  title: string;
  body?: string;
  className?: string;
}) {
  return (
    <div className={`flex min-h-32 flex-col items-center justify-center rounded-lg border border-dashed border-[var(--border)] bg-[var(--surface-inset)] px-4 py-8 text-center ${className}`}>
      {Icon && <Icon size={24} className="mb-2 text-[var(--text-subtle)]" />}
      <div className="text-sm font-medium text-[var(--text-muted)]">{title}</div>
      {body && <p className="mt-1 max-w-md text-xs text-[var(--text-subtle)]">{body}</p>}
    </div>
  );
}

export function StatusPill({
  children,
  tone = "muted",
  className = "",
}: {
  children: React.ReactNode;
  tone?: Tone;
  className?: string;
}) {
  const toneClass: Record<Tone, string> = {
    accent: "border-[color-mix(in_srgb,var(--accent)_42%,transparent)] bg-[var(--accent-soft)] text-[var(--accent)]",
    amber: "border-[color-mix(in_srgb,var(--warning)_42%,transparent)] bg-[var(--warning-soft)] text-[var(--warning)]",
    success: "border-[color-mix(in_srgb,var(--success)_42%,transparent)] bg-[var(--success-soft)] text-[var(--success)]",
    danger: "border-[color-mix(in_srgb,var(--danger)_42%,transparent)] bg-[var(--danger-soft)] text-[var(--danger)]",
    violet: "border-[color-mix(in_srgb,var(--violet)_42%,transparent)] bg-[var(--violet-soft)] text-[var(--violet)]",
    muted: "border-[var(--border)] bg-[var(--surface-muted)] text-[var(--text-muted)]",
  };
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium ${toneClass[tone]} ${className}`}>
      {children}
    </span>
  );
}

export function TableShell({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <div className={`eflux-table-shell ${className}`}>{children}</div>;
}
