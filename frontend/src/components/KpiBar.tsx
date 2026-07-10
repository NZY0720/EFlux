import { Activity, Layers3, Scale, Zap, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import type { MarketSnapshot } from "../api/types";
import { formatPrice } from "../lib/format";

interface Props {
  snapshot: MarketSnapshot | null;
  builtinVpps: number;
  /** "p2p" (default) shows local market values; realprice uses the CAISO quote. */
  variant?: "p2p" | "realprice";
  /** Tight first-viewport treatment used by the market overviews. */
  compact?: boolean;
}

function ratio(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return Math.abs(value) < 0.005 ? "0" : `${value.toFixed(2)}x`;
}

function Cell({
  label,
  value,
  sub,
  icon: Icon,
  tone = "accent",
  compact,
}: {
  label: string;
  value: ReactNode;
  sub: string;
  icon: LucideIcon;
  tone?: "accent" | "success" | "warning" | "danger" | "muted";
  compact: boolean;
}) {
  const toneClass = {
    accent: "text-[var(--accent)]",
    success: "text-[var(--success)]",
    warning: "text-[var(--warning)]",
    danger: "text-[var(--danger)]",
    muted: "text-[var(--text-subtle)]",
  }[tone];

  return (
    <div className={`lg-frost lg-interactive eflux-card lg-stagger-item min-w-0 ${compact ? "px-3 py-2.5" : "px-4 py-3"}`}>
      <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
        <Icon size={14} className={toneClass} />
        {label}
      </div>
      <div className={`${compact ? "mt-0.5 text-xl" : "mt-1 text-2xl"} font-semibold tabular-nums text-[var(--text)]`}>{value}</div>
      <div className="mt-0.5 truncate text-xs text-[var(--text-subtle)]">{sub}</div>
    </div>
  );
}

export default function KpiBar({ snapshot, builtinVpps, variant = "p2p", compact = false }: Props) {
  const balance = snapshot?.balance;
  const external = snapshot?.external_market;
  const externalLive = external?.status === "real" || external?.status === "fallback";
  const latestPrice = variant === "realprice" && externalLive ? external?.raw_lmp : snapshot?.last_price;
  const spread = variant === "realprice"
    ? externalLive && external ? Number(external.import_price) - Number(external.export_price) : null
    : snapshot?.best_ask !== null && snapshot?.best_ask !== undefined && snapshot?.best_bid !== null && snapshot?.best_bid !== undefined
      ? Number(snapshot.best_ask) - Number(snapshot.best_bid)
      : null;
  const supplyDetail = balance
    ? `${balance.renewable_kw.toFixed(0)} kW supply / ${balance.load_kw.toFixed(0)} kW load`
    : "live capacity vs load";

  return (
    <div className="lg-stagger grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <Cell
        label={variant === "realprice" ? "Grid price" : "Latest price"}
        value={formatPrice(latestPrice)}
        sub={variant === "realprice" ? "CAISO LMP ($/MWh)" : "last P2P trade ($/MWh)"}
        icon={Zap}
        tone="warning"
        compact={compact}
      />
      <Cell
        label="Spread"
        value={formatPrice(spread)}
        sub={variant === "realprice" ? "import minus export ($/MWh)" : "best ask minus bid ($/MWh)"}
        icon={Scale}
        tone="muted"
        compact={compact}
      />
      <Cell
        label="Supply / demand"
        value={ratio(balance?.supply_demand_ratio)}
        sub={supplyDetail}
        icon={Activity}
        tone="success"
        compact={compact}
      />
      <Cell
        label="Active agents"
        value={String(builtinVpps)}
        sub={variant === "realprice" ? "price-taking strategies" : "auto traders"}
        icon={Layers3}
        tone="accent"
        compact={compact}
      />
    </div>
  );
}
