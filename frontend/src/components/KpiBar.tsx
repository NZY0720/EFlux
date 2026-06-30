import { useState } from "react";
import { Activity, ArrowDownRight, ArrowUpRight, Gauge, Layers3, Scale, Zap, type LucideIcon } from "lucide-react";

import { setMarketSpeed } from "../api/client";
import type { MarketSnapshot } from "../api/types";
import { useAuth } from "../state/auth";

interface Props {
  snapshot: MarketSnapshot | null;
  builtinVpps: number;
  /** "p2p" (default) shows P2P price/book KPIs; "realprice" shows grid-price KPIs. */
  variant?: "p2p" | "realprice";
}

const SPEEDS = [1, 10, 100];

function Cell({
  label,
  value,
  sub,
  icon: Icon,
  tone = "accent",
}: {
  label: string;
  value: React.ReactNode;
  sub?: string;
  icon?: LucideIcon;
  tone?: "accent" | "success" | "warning" | "danger" | "muted";
}) {
  const toneClass = {
    accent: "text-[var(--accent)]",
    success: "text-[var(--success)]",
    warning: "text-[var(--warning)]",
    danger: "text-[var(--danger)]",
    muted: "text-[var(--text-subtle)]",
  }[tone];
  return (
    <div className="eflux-card min-w-0 px-4 py-3">
      <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]">
        {Icon && <Icon size={15} className={toneClass} />}
        {label}
      </div>
      <div className="mt-1 text-2xl font-semibold text-[var(--text)] tabular-nums">{value}</div>
      {sub && <div className="mt-0.5 truncate text-xs text-[var(--text-subtle)]">{sub}</div>}
    </div>
  );
}

function SpeedCell({ snapshot }: { snapshot: MarketSnapshot | null }) {
  const { token } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const speed = snapshot?.speed ?? 1;
  const sub = error ?? (speed === 1 ? "realtime" : "fast (no external orders)");

  const change = async (s: number) => {
    if (s === speed || busy) return;
    setBusy(true);
    setError(null);
    try {
      await setMarketSpeed(s); // KPI refreshes via the 1Hz snapshot poll
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!token) return <Cell label="Speed" value={`${speed}x`} sub={sub} icon={Gauge} tone="warning" />;

  return (
    <div className="eflux-card min-w-0 px-4 py-3">
      <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]">
        <Gauge size={15} className="text-[var(--warning)]" />
        Speed
      </div>
      <div className="mt-1.5 inline-flex overflow-hidden rounded-md border border-[var(--border)] bg-[var(--surface-inset)]">
        {SPEEDS.map((s) => (
          <button
            key={s}
            onClick={() => change(s)}
            disabled={busy}
            className={`px-2.5 py-1 text-sm font-medium tabular-nums transition-colors disabled:opacity-60 ${
              speed === s ? "bg-[var(--accent-strong)] text-[var(--accent-contrast)]" : "text-[var(--text-muted)] hover:bg-[var(--surface-hover)] hover:text-[var(--text)]"
            }`}
          >
            {s}x
          </button>
        ))}
      </div>
      <div className={`mt-1 truncate text-xs ${error ? "text-[var(--danger)]" : "text-[var(--text-subtle)]"}`}>{sub}</div>
    </div>
  );
}

export default function KpiBar({ snapshot, builtinVpps, variant = "p2p" }: Props) {
  const fmt = (v: string | null | undefined) => (v === null || v === undefined ? "—" : Number(v).toFixed(2));
  const balance = snapshot?.balance;
  const external = snapshot?.external_market;
  // Only treat the CAISO price as live (and comparable) when it comes from a
  // real/fallback feed; synthetic/disabled is just the configured placeholder.
  const externalLive = external?.status === "real" || external?.status === "fallback";

  if (variant === "realprice") {
    // The market clears at the live CAISO price; agents are price-takers, so the
    // grid quote and its bid-ask spread are the KPIs that matter here.
    const spread =
      externalLive && external ? Number(external.import_price) - Number(external.export_price) : null;
    return (
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <Cell
          label="CAISO grid price"
          value={externalLive ? fmt(external?.raw_lmp) : "—"}
          sub={
            externalLive && external
              ? `buy ${Number(external.import_price).toFixed(2)} / sell ${Number(external.export_price).toFixed(2)} $/MWh`
              : external
                ? `${external.status} — no live feed`
                : "external market"
          }
          icon={Zap}
          tone="warning"
        />
        <Cell
          label="Grid spread"
          value={spread == null ? "—" : spread.toFixed(2)}
          sub="import minus export ($/MWh)"
          icon={Scale}
          tone="muted"
        />
        <Cell
          label="Supply / demand"
          value={balance?.supply_demand_ratio != null ? `${balance.supply_demand_ratio.toFixed(2)}x` : "—"}
          sub={
            balance
              ? `${balance.renewable_kw.toFixed(0)} kW renew vs ${balance.load_kw.toFixed(0)} kW load`
              : "live capacity vs load"
          }
          icon={Activity}
          tone="success"
        />
        <SpeedCell snapshot={snapshot} />
        <Cell label="Strategies" value={String(builtinVpps)} sub="price-taking agents" icon={Layers3} tone="accent" />
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-6">
      <Cell label="Last price" value={fmt(snapshot?.last_price)} sub="last P2P trade ($/MWh)" icon={Zap} tone="warning" />
      <Cell label="Best bid" value={fmt(snapshot?.best_bid)} sub="$/MWh" icon={ArrowDownRight} tone="success" />
      <Cell label="Best ask" value={fmt(snapshot?.best_ask)} sub="$/MWh" icon={ArrowUpRight} tone="danger" />
      <Cell
        label="Supply / demand"
        value={balance?.supply_demand_ratio != null ? `${balance.supply_demand_ratio.toFixed(2)}x` : "—"}
        sub={
          balance
            ? `${balance.renewable_kw.toFixed(0)} kW renew + ${balance.gas_capacity_kw.toFixed(0)} kW gas vs ${balance.load_kw.toFixed(0)} kW load`
          : "live capacity vs load"
        }
        icon={Scale}
        tone="accent"
      />
      <SpeedCell snapshot={snapshot} />
      <Cell label="Built-in VPPs" value={String(builtinVpps)} sub="auto traders" icon={Layers3} tone="muted" />
    </div>
  );
}
