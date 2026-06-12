import ReactECharts from "echarts-for-react";
import { useMemo } from "react";

import type { MarketEvent } from "../api/types";

interface PricePoint {
  ts: number; // ms
  price: number;
}

interface Props {
  events: MarketEvent[];
  windowMs?: number;
  initialPrice?: number | null;
}

/**
 * Streaming price line. Pulls last_price from tick events and trade prices.
 * Keeps the trailing `windowMs` of data.
 *
 * Points are derived purely from the (already deduped) provider buffer on
 * every render — no incremental state. An earlier version accumulated points
 * in component state keyed by a seen-set ref; under StrictMode's double
 * effect invocation the second run saw a stale empty closure plus a fully
 * populated seen-set and overwrote the rebuilt history with a single seed
 * point, which wiped the chart on every route remount.
 */
export default function PriceChart({ events, windowMs = 5 * 60 * 1000, initialPrice }: Props) {
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
    const cutoff = Date.now() - windowMs;
    const trimmed = pts.filter((p) => p.ts >= cutoff);
    if (trimmed.length === 0 && initialPrice !== null && initialPrice !== undefined) {
      trimmed.push({ ts: Date.now(), price: initialPrice });
    }
    return trimmed;
  }, [events, windowMs, initialPrice]);

  const option = {
    backgroundColor: "transparent",
    grid: { left: 50, right: 20, top: 30, bottom: 30 },
    xAxis: {
      type: "time",
      axisLabel: { color: "#94a3b8" },
      splitLine: { lineStyle: { color: "#1e293b" } },
    },
    yAxis: {
      type: "value",
      scale: true,
      name: "price ($/kWh)",
      nameTextStyle: { color: "#64748b", fontSize: 11 },
      axisLabel: { color: "#94a3b8" },
      splitLine: { lineStyle: { color: "#1e293b" } },
    },
    tooltip: { trigger: "axis", backgroundColor: "#1e293b", borderWidth: 0, textStyle: { color: "#e2e8f0" } },
    series: [
      {
        type: "line",
        showSymbol: false,
        smooth: false,
        sampling: "lttb",
        data: points.map((p) => [p.ts, p.price]),
        lineStyle: { color: "#38bdf8", width: 1.5 },
        areaStyle: { color: "rgba(56, 189, 248, 0.1)" },
      },
    ],
    animation: false,
  };

  return (
    <div className="h-72 w-full">
      <ReactECharts option={option} style={{ height: "100%", width: "100%" }} notMerge lazyUpdate />
    </div>
  );
}
