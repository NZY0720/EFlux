import { useState } from "react";

import { setMarketSpeed } from "../api/client";
import type { MarketSnapshot } from "../api/types";
import { useAuth } from "../state/auth";
import { BoltIcon, GaugeIcon, ScaleIcon, TrendDownIcon, TrendUpIcon, VppIcon, type IconProps } from "./icons";

interface Props {
  snapshot: MarketSnapshot | null;
  builtinVpps: number;
}

const SPEEDS = [1, 10, 100];

function Cell({
  label,
  value,
  sub,
  icon: Icon,
}: {
  label: string;
  value: React.ReactNode;
  sub?: string;
  icon?: (p: IconProps) => React.ReactElement;
}) {
  return (
    <div className="eflux-card flex-1 min-w-[140px] px-4 py-3 border border-slate-800 rounded-lg bg-slate-900/60">
      <div className="flex items-center gap-1.5 text-xs uppercase tracking-wide text-slate-400">
        {Icon && <Icon size={14} className="text-slate-500" />}
        {label}
      </div>
      <div className="text-2xl font-semibold text-white mt-1 tabular-nums">{value}</div>
      {sub && <div className="text-xs text-slate-500 mt-0.5">{sub}</div>}
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

  if (!token) return <Cell label="Speed" value={`${speed}x`} sub={sub} icon={GaugeIcon} />;

  return (
    <div className="eflux-card flex-1 min-w-[140px] px-4 py-3 border border-slate-800 rounded-lg bg-slate-900/60">
      <div className="flex items-center gap-1.5 text-xs uppercase tracking-wide text-slate-400">
        <GaugeIcon size={14} className="text-slate-500" />
        Speed
      </div>
      <div className="mt-1.5 inline-flex overflow-hidden rounded border border-slate-700">
        {SPEEDS.map((s) => (
          <button
            key={s}
            onClick={() => change(s)}
            disabled={busy}
            className={`px-2.5 py-1 text-sm tabular-nums transition-colors disabled:opacity-60 ${
              speed === s ? "bg-sky-600 text-white" : "bg-slate-800 text-slate-300 hover:bg-slate-700"
            }`}
          >
            {s}x
          </button>
        ))}
      </div>
      <div className={`text-xs mt-1 ${error ? "text-rose-400" : "text-slate-500"}`}>{sub}</div>
    </div>
  );
}

export default function KpiBar({ snapshot, builtinVpps }: Props) {
  const fmt = (v: string | null | undefined) => (v === null || v === undefined ? "—" : Number(v).toFixed(2));
  const balance = snapshot?.balance;
  const external = snapshot?.external_market;
  // Only treat the CAISO price as live (and comparable) when it comes from a
  // real/fallback feed; synthetic/disabled is just the configured placeholder.
  const externalLive = external?.status === "real" || external?.status === "fallback";
  const p2pBasis =
    snapshot?.last_price && externalLive && external?.raw_lmp
      ? Number(snapshot.last_price) - Number(external.raw_lmp)
      : null;
  return (
    <div className="flex flex-wrap gap-3">
      <Cell label="Last price" value={fmt(snapshot?.last_price)} sub="last P2P trade ($/MWh)" icon={BoltIcon} />
      <Cell
        label="CAISO SP15"
        value={externalLive ? fmt(external?.raw_lmp) : "—"}
        sub={
          externalLive && external
            ? `buy ${Number(external.import_price).toFixed(2)} / sell ${Number(external.export_price).toFixed(2)} $/MWh`
            : external
              ? `${external.status} — no live feed`
              : "external market"
        }
        icon={BoltIcon}
      />
      <Cell
        label="P2P basis"
        value={p2pBasis == null ? "—" : p2pBasis.toFixed(2)}
        sub="P2P last minus CAISO ($/MWh)"
        icon={ScaleIcon}
      />
      <Cell label="Best bid" value={fmt(snapshot?.best_bid)} sub="$/MWh" icon={TrendDownIcon} />
      <Cell label="Best ask" value={fmt(snapshot?.best_ask)} sub="$/MWh" icon={TrendUpIcon} />
      <Cell
        label="Supply / demand"
        value={balance?.supply_demand_ratio != null ? `${balance.supply_demand_ratio.toFixed(2)}x` : "—"}
        sub={
          balance
            ? `${balance.renewable_kw.toFixed(0)} kW renew + ${balance.gas_capacity_kw.toFixed(0)} kW gas vs ${balance.load_kw.toFixed(0)} kW load`
            : "live capacity vs load"
        }
        icon={ScaleIcon}
      />
      <SpeedCell snapshot={snapshot} />
      <Cell label="Built-in VPPs" value={String(builtinVpps)} sub="auto traders" icon={VppIcon} />
    </div>
  );
}
