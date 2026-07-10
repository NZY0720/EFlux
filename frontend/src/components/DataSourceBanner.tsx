import { Clock3, DatabaseZap } from "lucide-react";

import type { DataSourceStatus } from "../api/types";
import type { DataSourceEntry } from "../api/types";
import { StatusPill } from "./DashboardCard";

interface Props {
  dataSource?: DataSourceStatus;
  /** Show the external (CAISO) price source. False for the pure-P2P market, which doesn't use CAISO. */
  showExternalPrice?: boolean;
}

function statusTone(status: string): "success" | "amber" | "muted" {
  if (status === "real") return "success";
  if (status === "fallback") return "amber";
  return "muted";
}

function isSource(source: DataSourceEntry | undefined): source is DataSourceEntry {
  return source !== undefined;
}

export default function DataSourceBanner({ dataSource, showExternalPrice = true }: Props) {
  const checkedAt = dataSource
    ? new Date(dataSource.checked_at).toLocaleTimeString("en-GB", { hour12: false })
    : "checking";
  const weather = dataSource?.sources.find((s) => !s.component.includes("CAISO")) ?? dataSource?.sources[0];
  const price = showExternalPrice ? dataSource?.sources.find((s) => s.component.includes("CAISO")) : undefined;
  const visibleSources =
    weather && price && weather.component === price.component ? [price] : [weather, price].filter(isSource);

  return (
    <section className="lg-frost eflux-panel px-4 py-3">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div className="flex min-w-0 gap-3">
          <DatabaseZap size={18} className="mt-1 shrink-0 text-[var(--accent)]" />
          <div className="min-w-0">
            <div className="text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]">Data source</div>
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <span className="break-words text-base font-semibold text-[var(--text)]">
                {dataSource?.summary ?? "Checking startup source"}
              </span>
              {price && <StatusPill tone={statusTone(price.status)}>{price.status}</StatusPill>}
            </div>
            <div className="mt-1 space-y-1">
              {visibleSources.map((source) => (
                <p key={source.component} className="break-words text-sm text-[var(--text-muted)]">
                  {source.component}: {source.source}. {source.detail}
                </p>
              ))}
            </div>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1.5 text-xs text-[var(--text-subtle)]">
          <Clock3 size={13} />
          checked {checkedAt}
        </div>
      </div>
    </section>
  );
}
