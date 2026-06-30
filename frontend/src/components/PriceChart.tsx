import ReactECharts from "echarts-for-react";
import { useMemo, useState } from "react";

import type { MarketEvent } from "../api/types";
import { chartAxis, chartLegend, chartTooltip, useChartTheme } from "./chartTheme";
import { FULL_ZOOM, timeZoom, usePersistentTimeZoom } from "./chartZoom";

interface PricePoint {
  ts: number; // ms
  price: number;
}

interface Candle {
  t: number; // bucket-start ms
  o: number;
  h: number;
  l: number;
  c: number;
}

type Mode = "line" | "candles";
const INTERVALS = [
  { sec: 10, label: "10s" },
  { sec: 30, label: "30s" },
  { sec: 60, label: "1m" },
] as const;

interface Props {
  events: MarketEvent[];
  initialPrice?: number | null;
  initialExternalPrice?: number | null;
  /**
   * "p2p" (default): blue emergent P2P line + dashed CAISO reference, with an
   * optional OHLC candle view.
   * "realprice": the CAISO grid price is the primary (and only) line — there is
   * no P2P book in that market, so last_price never updates and candles are hidden.
   */
  variant?: "p2p" | "realprice";
}

const fmtTime = (ms: number) => new Date(ms).toLocaleTimeString("en-GB", { hour12: false });

/**
 * Streaming price view that retains all history since the session started and
 * exposes a draggable time-zoom axis to inspect any window:
 *  - Line: raw price over time (last_price from tick events + each trade price).
 *  - Candles (p2p only): OHLC aggregated into 10s / 30s / 1m buckets.
 *
 * Points are derived from the (already deduped) provider buffer on every render —
 * no incremental state — so route remounts rebuild full history rather than
 * wiping the chart.
 */
export default function PriceChart({ events, initialPrice, initialExternalPrice, variant = "p2p" }: Props) {
  const [mode, setMode] = useState<Mode>("line");
  const [intervalSec, setIntervalSec] = useState<number>(30);
  const theme = useChartTheme();
  const allowCandles = variant === "p2p";
  const effectiveMode: Mode = allowCandles ? mode : "line";

  // Persist the user's zoom window (absolute time) across streaming rebuilds.
  // Mutated on the echarts "datazoom" event and re-applied to the option each
  // render, so a 1Hz data tick (notMerge) doesn't snap the view back to full range.
  const { zoomRef, onEvents } = usePersistentTimeZoom();

  // All price points in the buffer (oldest first): trade prints + per-tick
  // last_price. Full session history — the time-zoom slider trims the view.
  const points = useMemo(() => {
    const pts: PricePoint[] = [];
    for (const e of events) {
      let price: number | null = null;
      if (e.kind === "trade") {
        price = Number(e.price);
      } else if (e.kind === "tick") {
        if (e.last_price !== null && e.last_price !== undefined) price = Number(e.last_price);
      }
      if (price !== null && Number.isFinite(price)) {
        pts.push({ ts: new Date(e.wall_ts).getTime(), price });
      }
    }
    pts.sort((a, b) => a.ts - b.ts);
    return pts;
  }, [events]);

  const externalPoints = useMemo(() => {
    const pts: PricePoint[] = [];
    for (const e of events) {
      if (e.kind !== "tick" || e.external_price === null || e.external_price === undefined) continue;
      const price = Number(e.external_price);
      if (Number.isFinite(price)) pts.push({ ts: new Date(e.wall_ts).getTime(), price });
    }
    pts.sort((a, b) => a.ts - b.ts);
    return pts;
  }, [events]);

  const linePoints = useMemo(() => {
    if (points.length === 0 && initialPrice !== null && initialPrice !== undefined) {
      return [{ ts: Date.now(), price: initialPrice }];
    }
    return points;
  }, [points, initialPrice]);

  const lineExternalPoints = useMemo(() => {
    if (externalPoints.length === 0 && initialExternalPrice !== null && initialExternalPrice !== undefined) {
      return [{ ts: Date.now(), price: initialExternalPrice }];
    }
    return externalPoints;
  }, [externalPoints, initialExternalPrice]);

  // Which series the candles/line treat as primary. The real-price market has no
  // P2P book, so its primary price is the CAISO (external) feed.
  const primaryPoints = variant === "realprice" ? externalPoints : points;

  // OHLC buckets. Points are sorted ascending, so the first in a bucket is the
  // open and the last is the close. Keep the full session; the zoom slider trims.
  const candles = useMemo(() => {
    if (effectiveMode !== "candles") return [] as Candle[];
    const bucketMs = intervalSec * 1000;
    const byBucket = new Map<number, Candle>();
    for (const p of primaryPoints) {
      const t = Math.floor(p.ts / bucketMs) * bucketMs;
      const cur = byBucket.get(t);
      if (!cur) {
        byBucket.set(t, { t, o: p.price, h: p.price, l: p.price, c: p.price });
      } else {
        cur.h = Math.max(cur.h, p.price);
        cur.l = Math.min(cur.l, p.price);
        cur.c = p.price;
      }
    }
    return [...byBucket.values()].sort((a, b) => a.t - b.t);
  }, [primaryPoints, effectiveMode, intervalSec]);

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

  const lineOption = {
    backgroundColor: "transparent",
    legend: { top: 0, right: 12, ...chartLegend(theme) },
    grid: { left: 50, right: 20, top: 32, bottom: 56 },
    xAxis: { type: "time", ...baseAxis },
    yAxis: {
      type: "value",
      scale: true,
      name: "price ($/MWh)",
      nameTextStyle: { color: theme.muted, fontSize: 11 },
      ...baseAxis,
    },
    tooltip: { trigger: "axis", ...chartTooltip(theme) },
    dataZoom: timeZoom(zoomRef.current, zoomTheme),
    series:
      variant === "realprice"
        ? [
            // Real-price market: the live CAISO price IS the market — make it primary.
            {
              type: "line",
              name: "CAISO (grid price)",
              showSymbol: false,
              smooth: false,
              sampling: "lttb",
              data: lineExternalPoints.map((p) => [p.ts, p.price]),
              lineStyle: { color: theme.warning, width: 1.8 },
              areaStyle: { color: "rgba(245, 158, 11, 0.12)" },
            },
          ]
        : [
            // Pure P2P: only the emergent local price. CAISO is not shown in this market.
            {
              type: "line",
              name: "P2P",
              showSymbol: false,
              smooth: false,
              sampling: "lttb",
              data: linePoints.map((p) => [p.ts, p.price]),
              lineStyle: { color: theme.accent, width: 1.8 },
              areaStyle: { color: "rgba(34, 183, 232, 0.12)" },
            },
          ],
    animation: false,
  };

  const candleOption = {
    backgroundColor: "transparent",
    grid: { left: 50, right: 20, top: 16, bottom: 64 },
    xAxis: {
      type: "category",
      data: candles.map((c) => fmtTime(c.t)),
      boundaryGap: true,
      ...baseAxis,
      axisLabel: { color: theme.axis, hideOverlap: true },
    },
    yAxis: {
      type: "value",
      scale: true,
      name: "price ($/MWh)",
      nameTextStyle: { color: theme.muted, fontSize: 11 },
      ...baseAxis,
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross" },
      ...chartTooltip(theme),
    },
    dataZoom: timeZoom(zoomRef.current, zoomTheme),
    series: [
      {
        type: "candlestick",
        // ECharts candlestick value order: [open, close, low, high].
        data: candles.map((c) => [c.o, c.c, c.l, c.h]),
        itemStyle: {
          color: theme.success, // bullish body (close >= open)
          color0: theme.danger, // bearish body
          borderColor: theme.success,
          borderColor0: theme.danger,
        },
      },
    ],
    animation: false,
  };

  const hasCandles = candles.length > 0;

  const segBtn = (active: boolean) =>
    `px-2.5 py-1 text-xs font-medium transition-colors ${
      active ? "bg-[var(--accent-strong)] text-[var(--accent-contrast)]" : "text-[var(--text-muted)] hover:bg-[var(--surface-hover)] hover:text-[var(--text)]"
    }`;

  return (
    <div className="space-y-2">
      {allowCandles && (
        <div className="flex items-center justify-end gap-2">
          {effectiveMode === "candles" && (
            <div className="inline-flex overflow-hidden rounded-md border border-[var(--border)] bg-[var(--surface-inset)]">
              {INTERVALS.map((iv) => (
                <button key={iv.sec} onClick={() => setIntervalSec(iv.sec)} className={segBtn(intervalSec === iv.sec)}>
                  {iv.label}
                </button>
              ))}
            </div>
          )}
          <div className="inline-flex overflow-hidden rounded-md border border-[var(--border)] bg-[var(--surface-inset)]">
            <button
              onClick={() => {
                setMode("line");
                zoomRef.current = FULL_ZOOM; // line uses a time axis; reset the category-axis zoom
              }}
              className={segBtn(effectiveMode === "line")}
            >
              Line
            </button>
            <button
              onClick={() => {
                setMode("candles");
                zoomRef.current = FULL_ZOOM; // candles use a category axis; reset the time-axis zoom
              }}
              className={segBtn(effectiveMode === "candles")}
            >
              Candles
            </button>
          </div>
        </div>
      )}
      <div className="h-72 w-full">
        {effectiveMode === "candles" && !hasCandles ? (
          <div className="flex h-full items-center justify-center text-sm text-[var(--text-subtle)]">
            Waiting for trades to aggregate…
          </div>
        ) : (
          <ReactECharts
            option={effectiveMode === "candles" ? candleOption : lineOption}
            style={{ height: "100%", width: "100%" }}
            onEvents={onEvents}
            notMerge
            lazyUpdate
          />
        )}
      </div>
    </div>
  );
}
