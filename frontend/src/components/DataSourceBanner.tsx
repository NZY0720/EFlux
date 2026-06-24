import type { DataSourceStatus } from "../api/types";
import type { DataSourceEntry } from "../api/types";

interface Props {
  dataSource?: DataSourceStatus;
}

function statusClasses(status: string): string {
  if (status === "real") return "border-emerald-800 bg-emerald-950/40 text-emerald-200";
  if (status === "fallback") return "border-amber-800 bg-amber-950/40 text-amber-200";
  return "border-slate-700 bg-slate-900 text-slate-300";
}

function isSource(source: DataSourceEntry | undefined): source is DataSourceEntry {
  return source !== undefined;
}

export default function DataSourceBanner({ dataSource }: Props) {
  const checkedAt = dataSource
    ? new Date(dataSource.checked_at).toLocaleTimeString("en-GB", { hour12: false })
    : "checking";
  const weather = dataSource?.sources.find((s) => !s.component.includes("CAISO")) ?? dataSource?.sources[0];
  const price = dataSource?.sources.find((s) => s.component.includes("CAISO"));
  const visibleSources =
    weather && price && weather.component === price.component ? [price] : [weather, price].filter(isSource);

  return (
    <section className="rounded-lg border border-slate-800 bg-slate-900/50 px-4 py-3">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <div className="text-xs uppercase tracking-wide text-slate-400">Data source</div>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <span className="text-base font-semibold text-white">
              {dataSource?.summary ?? "Checking startup source"}
            </span>
            {price && (
              <span className={`rounded border px-2 py-0.5 text-xs ${statusClasses(price.status)}`}>
                {price.status}
              </span>
            )}
          </div>
          <div className="mt-1 space-y-1">
            {visibleSources.map((source) => (
              <p key={source.component} className="text-sm text-slate-400">
                {source.component}: {source.source}. {source.detail}
              </p>
            ))}
          </div>
        </div>
        <div className="shrink-0 text-xs text-slate-500">checked {checkedAt}</div>
      </div>
    </section>
  );
}
