import type { MarketSnapshot } from "../api/types";

interface Props {
  snapshot: MarketSnapshot | null;
  builtinVpps: number;
}

function Cell({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="flex-1 min-w-[140px] px-4 py-3 border border-slate-800 rounded-lg bg-slate-900/60">
      <div className="text-xs uppercase tracking-wide text-slate-400">{label}</div>
      <div className="text-2xl font-semibold text-white mt-1 tabular-nums">{value}</div>
      {sub && <div className="text-xs text-slate-500 mt-0.5">{sub}</div>}
    </div>
  );
}

export default function KpiBar({ snapshot, builtinVpps }: Props) {
  const fmt = (v: string | null | undefined) => (v === null || v === undefined ? "—" : Number(v).toFixed(2));
  return (
    <div className="flex flex-wrap gap-3">
      <Cell label="Last price" value={fmt(snapshot?.last_price)} sub="last trade" />
      <Cell label="Best bid" value={fmt(snapshot?.best_bid)} />
      <Cell label="Best ask" value={fmt(snapshot?.best_ask)} />
      <Cell label="Speed" value={`${snapshot?.speed ?? 1}x`} sub={snapshot?.speed === 1 ? "realtime" : "fast (no external orders)"} />
      <Cell label="Built-in VPPs" value={String(builtinVpps)} sub="auto traders" />
    </div>
  );
}
