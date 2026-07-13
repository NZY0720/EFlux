import { Bot, Database, FlaskConical, History } from "lucide-react";
import { Link, useLocation } from "react-router-dom";

type WorkspaceItem = {
  to: string;
  label: string;
  description: string;
  icon: typeof Bot;
  active: (pathname: string) => boolean;
};

function WorkspaceNav({
  title,
  description,
  items,
}: {
  title: string;
  description: string;
  items: WorkspaceItem[];
}) {
  const { pathname } = useLocation();

  return (
    <header className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold text-[var(--text)]">{title}</h1>
        <p className="mt-1 max-w-3xl text-sm text-[var(--text-muted)]">
          {description}
        </p>
      </div>
      <nav
        aria-label={`${title} sections`}
        className="flex flex-wrap gap-2 border-b border-[var(--border)] pb-3"
      >
        {items.map((item) => {
          const Icon = item.icon;
          const selected = item.active(pathname);
          return (
            <Link
              key={item.to}
              to={item.to}
              aria-current={selected ? "page" : undefined}
              title={item.description}
              className={`eflux-btn h-9 px-3 text-sm ${selected ? "eflux-tab-active" : ""}`}
            >
              <Icon size={15} /> {item.label}
            </Link>
          );
        })}
      </nav>
    </header>
  );
}

export function EvaluationNav() {
  return (
    <WorkspaceNav
      title="Evaluate"
      description="Test a portfolio privately, then compare it with reproducible reference runs."
      items={[
        {
          to: "/evaluate/quick-test",
          label: "Quick test",
          description: "Private historical prove-out",
          icon: FlaskConical,
          active: (pathname) => pathname.startsWith("/evaluate/quick-test"),
        },
        {
          to: "/evaluate/runs",
          label: "Runs",
          description: "Private, release-bound and reference evidence",
          icon: History,
          active: (pathname) => pathname.startsWith("/evaluate/runs"),
        },
      ]}
    />
  );
}

export function AgentsNav() {
  return (
    <WorkspaceNav
      title="Agents"
      description="Move from trusted training data to an immutable agent release, evaluation and deployment."
      items={[
        {
          to: "/agents",
          label: "Releases",
          description: "Versioned agent recipes and runtime state",
          icon: Bot,
          active: (pathname) =>
            pathname === "/agents" || pathname.startsWith("/agents/releases"),
        },
        {
          to: "/agents/training-data",
          label: "Training data",
          description: "Behavior datasets for BC and PPO training",
          icon: Database,
          active: (pathname) => pathname.startsWith("/agents/training-data"),
        },
      ]}
    />
  );
}
