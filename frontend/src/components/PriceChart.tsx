import ReactECharts from "echarts-for-react";
import { useEffect, useRef, useState } from "react";

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
 */
export default function PriceChart({ events, windowMs = 5 * 60 * 1000, initialPrice }: Props) {
  const [points, setPoints] = useState<PricePoint[]>([]);
  const seenRef = useRef(new Set<string>());

  useEffect(() => {
    let updated = false;
    const next = points.slice();

    // Seed with initial price if we have nothing yet.
    if (next.length === 0 && initialPrice !== null && initialPrice !== undefined) {
      next.push({ ts: Date.now(), price: initialPrice });
      updated = true;
    }

    for (const e of events) {
      let price: number | null = null;
      const key = `${e.kind}-${(e as { trade_id?: number }).trade_id ?? (e as { tick_no?: number }).tick_no ?? e.wall_ts}`;
      if (seenRef.current.has(key)) continue;
      seenRef.current.add(key);

      if (e.kind === "trade") {
        price = Number((e as { price: string }).price);
      } else if (e.kind === "tick") {
        const lp = (e as { last_price: string | null }).last_price;
        if (lp !== null && lp !== undefined) price = Number(lp);
      }
      if (price !== null && Number.isFinite(price)) {
        next.push({ ts: new Date(e.wall_ts).getTime(), price });
        updated = true;
      }
    }

    if (updated) {
      const cutoff = Date.now() - windowMs;
      const trimmed = next.filter((p) => p.ts >= cutoff);
      setPoints(trimmed);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events]);

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
