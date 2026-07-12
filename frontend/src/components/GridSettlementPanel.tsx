import type { MarketSnapshot } from "../api/types";
import { formatPrice } from "../lib/format";

function finite(value: number | string | null | undefined): number | null {
  const parsed = typeof value === "string" ? Number(value) : value;
  return parsed !== null && parsed !== undefined && Number.isFinite(parsed) ? parsed : null;
}

function QuoteRow({
  label,
  detail,
  value,
  valueClass,
}: {
  label: string;
  detail: string;
  value: number | null;
  valueClass: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-2.5">
      <dt className="min-w-0">
        <div className="text-sm font-medium text-[var(--text)]">{label}</div>
        <div className="mt-0.5 text-xs text-[var(--text-subtle)]">{detail}</div>
      </dt>
      <dd className={`shrink-0 font-mono text-lg font-semibold tabular-nums ${valueClass}`}>
        {value === null ? "N/A" : formatPrice(value)}
      </dd>
    </div>
  );
}

export default function GridSettlementPanel({ snapshot }: { snapshot: MarketSnapshot | null }) {
  const quote = snapshot?.external_market;
  const quoteAvailable = quote?.status === "real" || quote?.status === "fallback";
  const lmp = quoteAvailable ? finite(quote?.raw_lmp) : null;
  const importPrice = quoteAvailable ? finite(quote?.import_price) : null;
  const exportPrice = quoteAvailable ? finite(quote?.export_price) : null;
  const bandWidth = importPrice !== null && exportPrice !== null ? importPrice - exportPrice : null;
  const balance = snapshot?.balance;
  const netKw = finite(balance?.net_kw);
  const positionLabel = netKw === null
    ? "Waiting for telemetry"
    : netKw > 0.05
      ? "Renewable surplus"
      : netKw < -0.05
        ? "Renewable deficit"
        : "Renewables match load";
  const positionClass = netKw === null
    ? "text-[var(--text-subtle)]"
    : netKw >= 0
      ? "text-[var(--success)]"
      : "text-[var(--danger)]";

  return (
    <div className="grid gap-6 md:grid-cols-2 md:gap-8">
      <section aria-labelledby="grid-quote-heading">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h4 id="grid-quote-heading" className="text-sm font-semibold text-[var(--text)]">Side-specific execution prices</h4>
            <p className="mt-1 text-xs text-[var(--text-subtle)]">Every fill is price-taking against the grid.</p>
          </div>
          <span className="shrink-0 text-xs font-medium text-[var(--text-muted)]">$/MWh</span>
        </div>
        <dl className="mt-3 divide-y divide-[var(--border)] border-y border-[var(--border)]">
          <QuoteRow label="Buy from grid" detail="CAISO LMP plus transaction fee" value={importPrice} valueClass="text-[var(--danger)]" />
          <QuoteRow label="CAISO LMP" detail="Wholesale reference price" value={lmp} valueClass="text-[var(--warning)]" />
          <QuoteRow label="Sell to grid" detail="CAISO LMP minus transaction fee" value={exportPrice} valueClass="text-[var(--success)]" />
        </dl>
        <p className="mt-2 text-xs text-[var(--text-muted)]">
          Full buy/sell band: <span className="font-mono tabular-nums text-[var(--text)]">{bandWidth === null ? "N/A" : `${formatPrice(bandWidth)} $/MWh`}</span>
        </p>
      </section>

      <section aria-labelledby="grid-position-heading">
        <h4 id="grid-position-heading" className="text-sm font-semibold text-[var(--text)]">Local system position</h4>
        <p className="mt-1 text-xs text-[var(--text-subtle)]">Renewable output compared with current VPP load.</p>
        <div className="mt-4 border-l-2 border-[var(--border-strong)] pl-4">
          <div className={`font-mono text-2xl font-semibold tabular-nums ${positionClass}`}>
            {netKw === null ? "N/A" : `${netKw > 0 ? "+" : ""}${netKw.toFixed(1)} kW`}
          </div>
          <div className="mt-1 text-sm font-medium text-[var(--text)]">{positionLabel}</div>
        </div>
        <dl className="mt-4 grid grid-cols-2 gap-x-5 gap-y-3 text-xs">
          <div>
            <dt className="text-[var(--text-subtle)]">Renewables</dt>
            <dd className="mt-0.5 font-mono text-sm tabular-nums text-[var(--text)]">{balance ? `${balance.renewable_kw.toFixed(0)} kW` : "N/A"}</dd>
          </div>
          <div>
            <dt className="text-[var(--text-subtle)]">Load</dt>
            <dd className="mt-0.5 font-mono text-sm tabular-nums text-[var(--text)]">{balance ? `${balance.load_kw.toFixed(0)} kW` : "N/A"}</dd>
          </div>
          <div>
            <dt className="text-[var(--text-subtle)]">Dispatchable capacity</dt>
            <dd className="mt-0.5 font-mono text-sm tabular-nums text-[var(--text)]">{balance ? `${balance.gas_capacity_kw.toFixed(0)} kW` : "N/A"}</dd>
          </div>
          <div>
            <dt className="text-[var(--text-subtle)]">Active strategies</dt>
            <dd className="mt-0.5 font-mono text-sm tabular-nums text-[var(--text)]">{snapshot?.num_builtin_vpps ?? "N/A"}</dd>
          </div>
        </dl>
      </section>
    </div>
  );
}
