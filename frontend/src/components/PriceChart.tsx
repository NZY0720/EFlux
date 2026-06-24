import ReactECharts from "echarts-for-react";
import { useMemo, useState } from "react";

import type { MarketEvent } from "../api/types";

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
  windowMs?: number;
  initialPrice?: number | null;
  initialExternalPrice?: number | null;
}

const fmtTime = (ms: number) => new Date(ms).toLocaleTimeString("en-GB", { hour12: false });

/**
 * Streaming price view with two modes:
 *  - Line: raw price over time (last_price from tick events + each trade price).
 *  - Candles: OHLC aggregated into 10s / 30s / 1m buckets — smooths the bid-ask
 *    bounce of a thin book into readable open/high/low/close bars.
 *
 * Points are derived purely from the (already deduped) provider buffer on every
 * render — no incremental state — so route remounts rebuild full history rather
 * than wiping the chart.
 */
export default function PriceChart({ events, windowMs = 5 * 60 * 1000, initialPrice, initialExternalPrice }: Props) {
  const [mode, setMode] = useState<Mode>("line");
  const [intervalSec, setIntervalSec] = useState<number>(30);

  // All price points in the buffer (oldest first): trade prints + per-tick
  // last_price. Candles aggregate these; the line view trims to windowMs.
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
    const cutoff = Date.now() - windowMs;
    const trimmed = points.filter((p) => p.ts >= cutoff);
    if (trimmed.length === 0 && initialPrice !== null && initialPrice !== undefined) {
      trimmed.push({ ts: Date.now(), price: initialPrice });
    }
    return trimmed;
  }, [points, windowMs, initialPrice]);

  const lineExternalPoints = useMemo(() => {
    const cutoff = Date.now() - windowMs;
    const trimmed = externalPoints.filter((p) => p.ts >= cutoff);
    if (trimmed.length === 0 && initialExternalPrice !== null && initialExternalPrice !== undefined) {
      trimmed.push({ ts: Date.now(), price: initialExternalPrice });
    }
    return trimmed;
  }, [externalPoints, windowMs, initialExternalPrice]);

  // OHLC buckets. Points are sorted ascending, so the first in a bucket is the
  // open and the last is the close. Show the most recent ~80 candles.
  const candles = useMemo(() => {
    if (mode !== "candles") return [] as Candle[];
    const bucketMs = intervalSec * 1000;
    const byBucket = new Map<number, Candle>();
    for (const p of points) {
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
    return [...byBucket.values()].sort((a, b) => a.t - b.t).slice(-80);
  }, [points, mode, intervalSec]);

  const baseAxis = {
    axisLabel: { color: "#94a3b8" },
    splitLine: { lineStyle: { color: "#1e293b" } },
  };

  const lineOption = {
    backgroundColor: "transparent",
    legend: { top: 0, right: 12, textStyle: { color: "#94a3b8" } },
    grid: { left: 50, right: 20, top: 32, bottom: 30 },
    xAxis: { type: "time", ...baseAxis },
    yAxis: {
      type: "value",
      scale: true,
      name: "price ($/MWh)",
      nameTextStyle: { color: "#64748b", fontSize: 11 },
      ...baseAxis,
    },
    tooltip: { trigger: "axis", backgroundColor: "#1e293b", borderWidth: 0, textStyle: { color: "#e2e8f0" } },
    series: [
      {
        type: "line",
        name: "P2P",
        showSymbol: false,
        smooth: false,
        sampling: "lttb",
        data: linePoints.map((p) => [p.ts, p.price]),
        lineStyle: { color: "#38bdf8", width: 1.5 },
        areaStyle: { color: "rgba(56, 189, 248, 0.1)" },
      },
      {
        type: "line",
        name: "CAISO SP15",
        showSymbol: false,
        smooth: false,
        data: lineExternalPoints.map((p) => [p.ts, p.price]),
        lineStyle: { color: "#f59e0b", width: 1.5, type: "dashed" },
      },
    ],
    animation: false,
  };

  const candleOption = {
    backgroundColor: "transparent",
    grid: { left: 50, right: 20, top: 16, bottom: 40 },
    xAxis: {
      type: "category",
      data: candles.map((c) => fmtTime(c.t)),
      boundaryGap: true,
      axisLabel: { color: "#94a3b8", hideOverlap: true },
      axisLine: { lineStyle: { color: "#334155" } },
    },
    yAxis: {
      type: "value",
      scale: true,
      name: "price ($/MWh)",
      nameTextStyle: { color: "#64748b", fontSize: 11 },
      ...baseAxis,
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross" },
      backgroundColor: "#1e293b",
      borderWidth: 0,
      textStyle: { color: "#e2e8f0" },
    },
    series: [
      {
        type: "candlestick",
        // ECharts candlestick value order: [open, close, low, high].
        data: candles.map((c) => [c.o, c.c, c.l, c.h]),
        itemStyle: {
          color: "#10b981", // bullish body (close ≥ open)
          color0: "#f43f5e", // bearish body
          borderColor: "#34d399",
          borderColor0: "#fb7185",
        },
      },
    ],
    animation: false,
  };

  const hasCandles = candles.length > 0;

  const segBtn = (active: boolean) =>
    `px-2.5 py-1 text-xs transition-colors ${
      active ? "bg-sky-600 text-white" : "bg-slate-800 text-slate-300 hover:bg-slate-700"
    }`;

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-end gap-2">
        {mode === "candles" && (
          <div className="inline-flex overflow-hidden rounded border border-slate-700">
            {INTERVALS.map((iv) => (
              <button
                key={iv.sec}
                onClick={() => setIntervalSec(iv.sec)}
                className={segBtn(intervalSec === iv.sec)}
              >
                {iv.label}
              </button>
            ))}
          </div>
        )}
        <div className="inline-flex overflow-hidden rounded border border-slate-700">
          <button onClick={() => setMode("line")} className={segBtn(mode === "line")}>
            Line
          </button>
          <button onClick={() => setMode("candles")} className={segBtn(mode === "candles")}>
            Candles
          </button>
        </div>
      </div>
      <div className="h-64 w-full">
        {mode === "candles" && !hasCandles ? (
          <div className="flex h-full items-center justify-center text-sm text-slate-500">
            Waiting for trades to aggregate…
          </div>
        ) : (
          <ReactECharts
            option={mode === "candles" ? candleOption : lineOption}
            style={{ height: "100%", width: "100%" }}
            notMerge
            lazyUpdate
          />
        )}
      </div>
    </div>
  );
}
