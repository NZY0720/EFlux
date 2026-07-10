import ReactECharts from "echarts-for-react";
import { useEffect, useMemo, useState } from "react";
import { ChartNoAxesCombined, Maximize2 } from "lucide-react";

import { api, fetchForecastHistory, fetchLatestForecast } from "../api/client";
import type {
  ForecastHistoryRecord,
  ForecastHorizon,
  ForecastTarget,
  LatestForecastResponse,
  ForecastSkillResponse,
} from "../api/types";
import { CardTitle, DashboardCard, EmptyState, StatusPill } from "../components/DashboardCard";
import { chartAxis, chartLegend, chartTooltip, useChartTheme } from "../components/chartTheme";
import { timeZoom, usePersistentTimeZoom } from "../components/chartZoom";

type ViewMode = "outlook" | "accuracy";
type SeriesPoint = [number, number | null];

const HISTORY_LIMIT = 720;
const POLL_MS = 30_000;

const TARGETS: Array<{ value: ForecastTarget; label: string; unit: string }> = [
  { value: "price_real", label: "Grid price (CAISO)", unit: "$/MWh" },
  { value: "price_p2p", label: "P2P price", unit: "$/MWh" },
  { value: "ghi", label: "Solar irradiance (GHI)", unit: "W/m2" },
  { value: "temp_air", label: "Temperature", unit: "deg C" },
  { value: "wind_speed", label: "Wind speed", unit: "m/s" },
];

const HORIZONS: Array<{ key: ForecastHorizon; label: string; ms: number }> = [
  { key: "5m", label: "5m forecast", ms: 5 * 60_000 },
  { key: "1h", label: "1h forecast", ms: 60 * 60_000 },
  { key: "12h", label: "12h forecast", ms: 12 * 60 * 60_000 },
];

function asMs(ts: string): number | null {
  const ms = new Date(ts).getTime();
  return Number.isFinite(ms) ? ms : null;
}

function asNumber(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function formatDateTime(ts: string | null | undefined): string {
  if (!ts) return "-";
  const ms = asMs(ts);
  if (ms === null) return ts;
  return new Date(ms).toLocaleString([], {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[middle - 1] + sorted[middle]) / 2 : sorted[middle];
}

function insertHistoryGaps(points: SeriesPoint[], cadenceMs: number | null): SeriesPoint[] {
  if (cadenceMs === null) return points;
  return points.reduce<SeriesPoint[]>((withGaps, point) => {
    const previous = withGaps[withGaps.length - 1];
    if (previous && point[0] - previous[0] > cadenceMs * 2) {
      withGaps.push([previous[0] + cadenceMs, null]);
    }
    withGaps.push(point);
    return withGaps;
  }, []);
}

function useForecastPolling(target: ForecastTarget, limit = HISTORY_LIMIT) {
  const [history, setHistory] = useState<ForecastHistoryRecord[]>([]);
  const [latest, setLatest] = useState<LatestForecastResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    const load = async (initial: boolean) => {
      if (initial) {
        setLoading(true);
        setError(null);
      }
      try {
        const [nextLatest, nextHistory] = await Promise.all([
          fetchLatestForecast(),
          fetchForecastHistory(target, limit),
        ]);
        if (!active) return;
        setLatest(nextLatest);
        setHistory(nextHistory);
        setError(null);
      } catch (e) {
        if (!active) return;
        setError(e instanceof Error ? e.message : "Unable to load forecast data");
      } finally {
        if (active) setLoading(false);
      }
    };

    void load(true);
    const id = window.setInterval(() => void load(false), POLL_MS);
    return () => {
      active = false;
      window.clearInterval(id);
    };
  }, [limit, target]);

  return { history, latest, loading, error };
}

function useForecastSkillPolling() {
  const [skill, setSkill] = useState<ForecastSkillResponse | null>(null);
  useEffect(() => {
    let active = true;
    const load = () => api.get<ForecastSkillResponse>("/forecasts/skill").then(({ data }) => {
      if (active) setSkill(data);
    }).catch(() => {});
    void load();
    const id = window.setInterval(() => void load(), POLL_MS);
    return () => { active = false; window.clearInterval(id); };
  }, []);
  return skill;
}

export default function ForecastHub() {
  const [target, setTarget] = useState<ForecastTarget>("price_real");
  const [view, setView] = useState<ViewMode>("outlook");
  const [accuracyHorizon, setAccuracyHorizon] = useState<ForecastHorizon>("5m");
  const { history, latest, loading, error } = useForecastPolling(target);
  const skill = useForecastSkillPolling();
  const theme = useChartTheme();
  const { zoomRef, onEvents, autoFollow, resetZoom, setExtent } = usePersistentTimeZoom({ trackAutoFollow: true });

  const targetMeta = TARGETS.find((t) => t.value === target) ?? TARGETS[0];
  const warmingUp = latest != null && (latest.warm === false || latest.model_version === "empty");
  const latestMs = latest ? asMs(latest.as_of) : null;
  const historyNowMs = history.length > 0 ? asMs(history[history.length - 1].as_of) : null;
  const nowMs = latestMs ?? historyNowMs ?? Date.now();

  const rawRealizedPoints = useMemo(() => {
    const points: SeriesPoint[] = [];
    for (const record of history) {
      const ts = asMs(record.as_of);
      const value = asNumber(record.realized[target]);
      if (ts !== null && value !== null && ts <= nowMs) points.push([ts, value]);
    }
    return points;
  }, [history, nowMs, target]);

  const forecastCadenceMs = useMemo(() => {
    const timestamps = history.map((record) => asMs(record.as_of)).filter((ts): ts is number => ts !== null);
    const intervals = timestamps.slice(1).map((ts, index) => ts - timestamps[index]).filter((interval) => interval > 0);
    return median(intervals);
  }, [history]);

  const realizedPoints = useMemo(
    () => insertHistoryGaps(rawRealizedPoints, forecastCadenceMs),
    [forecastCadenceMs, rawRealizedPoints],
  );

  const historicalForecastPoints = useMemo(() => {
    const byHorizon: Record<ForecastHorizon, SeriesPoint[]> = { "5m": [], "1h": [], "12h": [] };
    for (const record of history) {
      const asOf = asMs(record.as_of);
      if (asOf === null || asOf > nowMs) continue;
      for (const horizon of HORIZONS) {
        const value = asNumber(record.forecasts[target]?.[horizon.key]);
        const targetMs = asOf + horizon.ms;
        if (value !== null && targetMs <= nowMs) byHorizon[horizon.key].push([targetMs, value]);
      }
    }
    return Object.fromEntries(
      HORIZONS.map((horizon) => [horizon.key, insertHistoryGaps(byHorizon[horizon.key], forecastCadenceMs)]),
    ) as Record<ForecastHorizon, SeriesPoint[]>;
  }, [forecastCadenceMs, history, nowMs, target]);

  const latestFanPoints = useMemo<Array<{ horizon: ForecastHorizon; label: string; data: SeriesPoint[] }>>(() => {
    const anchor = realizedPoints.length > 0 ? realizedPoints[realizedPoints.length - 1][1] : null;
    if (anchor === null || !latest) return [] as Array<{ horizon: ForecastHorizon; label: string; data: SeriesPoint[] }>;
    return HORIZONS.flatMap((horizon) => {
      const value = asNumber(latest[target]?.[horizon.key]?.value);
      if (value === null) return [];
      const data: SeriesPoint[] = [[nowMs, anchor], [nowMs + horizon.ms, value]];
      return [{ horizon: horizon.key, label: horizon.label, data }];
    });
  }, [latest, nowMs, realizedPoints, target]);

  const allChartPoints = useMemo(() => {
    const points: SeriesPoint[] = [...realizedPoints];
    if (view === "accuracy") {
      points.push(...historicalForecastPoints[accuracyHorizon]);
    } else {
      for (const fan of latestFanPoints) points.push(...fan.data);
    }
    return points;
  }, [accuracyHorizon, historicalForecastPoints, latestFanPoints, realizedPoints, view]);

  if (allChartPoints.length > 0) {
    const xs = allChartPoints.map(([ts]) => ts);
    setExtent(Math.min(...xs), Math.max(...xs));
  } else {
    setExtent(0, 0);
  }

  const chartOption = useMemo(() => {
    const baseAxis = chartAxis(theme);
    const zoomTheme = {
      bg: theme.surface,
      border: theme.tooltipBorder,
      filler: "rgba(34, 183, 232, 0.14)",
      handle: theme.axis,
      axis: theme.axis,
      grid: theme.grid,
      accent: theme.accent,
    };
    const colors: Record<ForecastHorizon, string> = {
      "5m": theme.accent,
      "1h": theme.warning,
      "12h": theme.violet,
    };
    const markLine = {
      silent: true,
      symbol: "none",
      label: { formatter: "now", color: theme.muted, fontSize: 11 },
      lineStyle: { color: theme.muted, type: "dashed", width: 1 },
      data: [{ xAxis: nowMs }],
    };

    const selectedHorizon = HORIZONS.find((horizon) => horizon.key === accuracyHorizon) ?? HORIZONS[0];
    const historicalSeries =
      view === "accuracy"
        ? [{
            type: "line" as const,
            name: `${selectedHorizon.label} — made ${selectedHorizon.label.replace(" forecast", "")} earlier`,
            showSymbol: false,
            smooth: false,
            sampling: "lttb",
            data: historicalForecastPoints[accuracyHorizon],
            lineStyle: { color: colors[accuracyHorizon], width: 1.6, opacity: 0.78 },
          }]
        : [];
    const currentFanSeries = view === "outlook" ? latestFanPoints.map((fan) => ({
            type: "line" as const,
            name: `${fan.label} (current)`,
            showSymbol: true,
            symbolSize: 6,
            smooth: false,
            data: fan.data,
            lineStyle: { color: colors[fan.horizon], width: 1.8, type: "dashed" },
            itemStyle: { color: colors[fan.horizon] },
          })) : [];

    return {
      backgroundColor: "transparent",
      legend: {
        top: 0,
        right: 12,
        ...chartLegend(theme),
        data: [
          "Realized",
          ...(view === "accuracy" ? [`${selectedHorizon.label} — made ${selectedHorizon.label.replace(" forecast", "")} earlier`] : []),
          ...(view === "outlook" ? HORIZONS.map((horizon) => `${horizon.label} (current)`) : []),
        ],
      },
      grid: { left: 58, right: 22, top: 34, bottom: 56 },
      xAxis: { type: "time", ...baseAxis },
      yAxis: {
        type: "value",
        scale: true,
        name: targetMeta.unit,
        nameTextStyle: { color: theme.muted, fontSize: 11 },
        ...baseAxis,
      },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" },
        valueFormatter: (value: unknown) => {
          const n = asNumber(value);
          return n === null ? "-" : `${n.toFixed(target.startsWith("price") ? 2 : 1)} ${targetMeta.unit}`;
        },
        ...chartTooltip(theme),
      },
      dataZoom: timeZoom(zoomRef.current, zoomTheme),
      series: [
        {
          type: "line" as const,
          name: "Realized",
          showSymbol: false,
          smooth: false,
          sampling: "lttb",
          data: realizedPoints,
          lineStyle: { color: theme.success, width: 2 },
          areaStyle: { color: "rgba(18, 201, 155, 0.08)" },
          markLine,
        },
        ...historicalSeries,
        ...currentFanSeries,
      ],
      animation: false,
    };
  }, [
    accuracyHorizon,
    historicalForecastPoints,
    latestFanPoints,
    nowMs,
    realizedPoints,
    target,
    targetMeta.unit,
    theme,
    view,
    zoomRef,
  ]);

  const hasChartData = allChartPoints.length > 0;
  const segBtn = (active: boolean) =>
    `px-3 py-1.5 text-xs font-medium transition-colors ${
      active
        ? "bg-[var(--accent-strong)] text-[var(--accent-contrast)]"
        : "text-[var(--text-muted)] hover:bg-[var(--surface-hover)] hover:text-[var(--text)]"
    }`;

  return (
    <div className="mx-auto w-full max-w-[1800px] space-y-6 px-4 py-5 md:p-6">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-semibold text-[var(--text)]">
          <ChartNoAxesCombined size={22} className="text-[var(--accent)]" />
          Forecasts
        </h1>
        <p className="mt-1 text-sm text-[var(--text-muted)]">
          {view === "outlook"
            ? "Dashed lines right of now are the model's current outlook."
            : "Each point shows what the model predicted for that moment, plotted at that moment — compare it against realized."}
        </p>
      </div>

      <DashboardCard>
        <CardTitle
          icon={ChartNoAxesCombined}
          action={
            latest ? (
              <div className="hidden text-right text-[11px] text-[var(--text-subtle)] sm:block">
                <div>{latest.model_version || "model unknown"}</div>
                <div>{formatDateTime(latest.as_of)}</div>
              </div>
            ) : null
          }
        >
          Forecast hub
        </CardTitle>

        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <select
              aria-label="Forecast target"
              value={target}
              onChange={(e) => {
                setTarget(e.target.value as ForecastTarget);
                resetZoom();
              }}
              className="eflux-select h-9 rounded-md px-3 text-sm"
            >
              {TARGETS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
            <div role="group" aria-label="Forecast view" className="inline-flex overflow-hidden rounded-md border border-[var(--border)] bg-[var(--surface-inset)]">
              <button type="button" aria-pressed={view === "outlook"} onClick={() => setView("outlook")} className={segBtn(view === "outlook")}>
                Outlook
              </button>
              <button type="button" aria-pressed={view === "accuracy"} onClick={() => setView("accuracy")} className={segBtn(view === "accuracy")}>
                Accuracy
              </button>
            </div>
            {view === "accuracy" && (
              <div role="group" aria-label="Forecast accuracy horizon" className="inline-flex overflow-hidden rounded-md border border-[var(--border)] bg-[var(--surface-inset)]">
                {HORIZONS.map((horizon) => (
                  <button
                    key={horizon.key}
                    type="button"
                    aria-pressed={accuracyHorizon === horizon.key}
                    onClick={() => setAccuracyHorizon(horizon.key)}
                    className={segBtn(accuracyHorizon === horizon.key)}
                  >
                    {horizon.key}
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill tone={error ? "danger" : loading ? "amber" : warmingUp ? "amber" : "success"}>
              {error
                ? "forecast API error"
                : loading
                  ? "loading"
                  : warmingUp
                    ? "warming up"
                    : `${history.length} records`}
            </StatusPill>
            <button
              type="button"
              onClick={resetZoom}
              title="Restore full forecast window"
              className={`inline-flex h-9 items-center gap-1.5 rounded-md border px-3 text-xs font-medium transition-colors ${
                autoFollow
                  ? "border-[var(--border)] text-[var(--text-muted)] hover:bg-[var(--surface-hover)] hover:text-[var(--text)]"
                  : "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent)] hover:bg-[var(--surface-hover)]"
              }`}
            >
              <Maximize2 size={13} />
              Auto Zoom
            </button>
          </div>
        </div>

        {error && <div className="mb-3 rounded-md border border-[color-mix(in_srgb,var(--danger)_35%,transparent)] bg-[var(--danger-soft)] px-3 py-2 text-sm text-[var(--danger)]">{error}</div>}

        {!error && warmingUp && (
          <div className="mb-3 rounded-md border border-[color-mix(in_srgb,var(--warning)_35%,transparent)] bg-[var(--warning-soft)] px-3 py-2 text-sm text-[var(--warning)]">
            Forecast models are warming up — published values are placeholders until the price
            models have real observations, and agents ignore them meanwhile.
          </div>
        )}

        <div className="lg-solid h-[420px] w-full p-1">
          {loading && !hasChartData ? (
            <EmptyState title="Loading forecast history..." className="h-full" />
          ) : !hasChartData ? (
            <EmptyState
              icon={ChartNoAxesCombined}
              title="No forecast history yet"
              body="The backend may have just started. This page will poll for new forecast records."
              className="h-full"
            />
          ) : (
            <ReactECharts
              key={view}
              option={chartOption}
              style={{ height: "100%", width: "100%" }}
              onEvents={onEvents}
              notMerge={false}
              lazyUpdate
            />
          )}
        </div>
      </DashboardCard>
      <DashboardCard>
        <CardTitle icon={ChartNoAxesCombined}>Model skill</CardTitle>
        <p className="mb-3 text-xs text-[var(--text-muted)]">skill = 1 − model MAE ÷ persistence MAE — above 0 beats naive persistence.</p>
        {!skill ? <EmptyState title="Loading scored outcomes..." /> : (
          <div className="grid gap-4 lg:grid-cols-2">
            {["24h", "7d"].map((window) => {
              const scores = skill.windows[window];
              return <div key={window} className="eflux-inset rounded-lg p-3"><div className="mb-2 font-mono text-xs font-semibold text-[var(--text)]">{window}</div>{!scores ? <p className="text-sm text-[var(--text-muted)]">No scored outcomes yet.</p> : <div className="space-y-3 font-mono text-[11px]"><div className="grid grid-cols-[1fr_34px_60px_60px_60px] gap-1 text-[var(--text-subtle)]"><span>target / horizon</span><span>n</span><span>mae</span><span>bias</span><span>skill</span></div>{["price_real", "price_p2p"].flatMap((targetName) => ["5m", "1h", "12h"].map((horizon) => { const metric = scores[targetName]?.[horizon]; const n = metric?.n ?? 0; const negative = (metric?.skill_vs_persistence ?? 0) < 0; return n === 0 ? <div key={`${targetName}-${horizon}`} className="grid grid-cols-[1fr_1fr] gap-1 text-[var(--text-muted)]"><span>{targetName === "price_real" ? "grid" : "p2p"} {horizon}</span><span>{horizon === "12h" ? "settles 12h after issue" : "no scored outcomes yet"}</span></div> : <div key={`${targetName}-${horizon}`} className="grid grid-cols-[1fr_34px_60px_60px_60px] gap-1"><span>{targetName === "price_real" ? "grid" : "p2p"} {horizon}</span><span>{n}</span><span>{metric?.mae?.toFixed(2) ?? "—"}</span><span>{metric?.bias?.toFixed(2) ?? "—"}</span><span className={negative ? "text-[var(--danger)]" : "text-[var(--text)]"}>{metric?.skill_vs_persistence == null ? "—" : metric.skill_vs_persistence.toFixed(2)}</span></div>; }))}</div>}</div>;
            })}
          </div>
        )}
        {latest && ["price_real", "price_p2p"].some((key) => HORIZONS.some((horizon) => { const provenance = latest[key as ForecastTarget]?.[horizon.key]?.provenance; return provenance?.includes("degraded") || provenance?.includes("cold_start"); })) && <div className="mt-3 flex flex-wrap gap-2">{["price_real", "price_p2p"].flatMap((key) => HORIZONS.map((horizon) => { const provenance = latest[key as ForecastTarget]?.[horizon.key]?.provenance; return provenance?.includes("degraded") || provenance?.includes("cold_start") ? <StatusPill key={`${key}-${horizon.key}`} tone="amber">{key} {horizon.key}: {provenance}</StatusPill> : null; }))}</div>}
      </DashboardCard>
    </div>
  );
}
