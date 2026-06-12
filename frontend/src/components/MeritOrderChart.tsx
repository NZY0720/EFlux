import ReactECharts from "echarts-for-react";
import { useEffect, useMemo, useState } from "react";

import { fetchSupplyCurve } from "../api/client";
import type { SupplyCurve } from "../api/types";
import { CATEGORY_ORDER, categoryMeta } from "../lib/categories";

interface Segment {
  x0: number;
  x1: number;
  price: number;
  name: string | null;
}

// Minimal slice of ECharts' custom-series render API that we use.
interface RenderApi {
  value: (i: number) => number;
  coord: (p: [number, number]) => [number, number];
  style: () => Record<string, unknown>;
}

/**
 * The supply stack: every resting ask drawn as a price-tall block, cheapest
 * first and colored by who offers it — the classic merit-order picture
 * (solar/wind floor → battery band → gas top). Cumulative demand (bids,
 * highest willingness-to-pay first) is overlaid as a dashed line; where the
 * curves meet is where trades clear.
 */
export default function MeritOrderChart() {
  const [curve, setCurve] = useState<SupplyCurve | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const c = await fetchSupplyCurve();
        if (!cancelled) setCurve(c);
      } catch {
        /* transient — keep the previous stack on screen */
      }
    };
    tick();
    const id = setInterval(tick, 2000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const { segmentsByCategory, demand } = useMemo(() => {
    const byCat = new Map<string, Segment[]>();
    let cum = 0;
    for (const o of curve?.asks ?? []) {
      const qty = Number(o.qty);
      const seg = { x0: cum, x1: cum + qty, price: Number(o.price), name: o.vpp_name };
      cum += qty;
      const list = byCat.get(o.category) ?? [];
      list.push(seg);
      byCat.set(o.category, list);
    }
    // Demand curve: bids arrive best (highest) first; step down as quantity accumulates.
    let dcum = 0;
    const demand: [number, number][] = [];
    for (const o of curve?.bids ?? []) {
      const price = Number(o.price);
      demand.push([dcum, price]);
      dcum += Number(o.qty);
      demand.push([dcum, price]);
    }
    return { segmentsByCategory: byCat, demand };
  }, [curve]);

  if (!curve) {
    return <div className="h-72 flex items-center justify-center text-slate-500">Loading supply stack…</div>;
  }
  if (curve.asks.length === 0 && curve.bids.length === 0) {
    return (
      <div className="h-72 flex items-center justify-center text-center text-sm text-slate-500">
        Order book is empty right now — the stack rebuilds within seconds as agents quote.
      </div>
    );
  }

  const renderBlock = (_params: unknown, api: RenderApi) => {
    const topLeft = api.coord([api.value(0), api.value(2)]);
    const bottomRight = api.coord([api.value(1), 0]);
    return {
      type: "rect",
      shape: {
        x: topLeft[0],
        y: topLeft[1],
        width: Math.max(bottomRight[0] - topLeft[0], 1), // keep slivers visible
        height: bottomRight[1] - topLeft[1],
      },
      style: api.style(),
    };
  };

  const stackSeries = CATEGORY_ORDER.filter((c) => segmentsByCategory.has(c)).map((cat) => {
    const meta = categoryMeta(cat);
    return {
      name: meta.label,
      type: "custom" as const,
      renderItem: renderBlock,
      encode: { x: [0, 1], y: 2 },
      data: (segmentsByCategory.get(cat) ?? []).map((s) => [s.x0, s.x1, s.price, s.name ?? "external VPP"]),
      itemStyle: { color: meta.color, opacity: 0.85, borderColor: "#0f172a", borderWidth: 0.5 },
    };
  });

  const option = {
    backgroundColor: "transparent",
    grid: { left: 55, right: 20, top: 44, bottom: 42 },
    legend: {
      top: 0,
      textStyle: { color: "#94a3b8", fontSize: 11 },
      itemWidth: 12,
      itemHeight: 8,
    },
    xAxis: {
      type: "value",
      name: "cumulative qty (kWh)",
      nameLocation: "middle",
      nameGap: 28,
      nameTextStyle: { color: "#64748b", fontSize: 11 },
      axisLabel: { color: "#94a3b8" },
      splitLine: { lineStyle: { color: "#1e293b" } },
    },
    yAxis: {
      type: "value",
      name: "price ($/kWh)",
      nameTextStyle: { color: "#64748b", fontSize: 11 },
      axisLabel: { color: "#94a3b8" },
      splitLine: { lineStyle: { color: "#1e293b" } },
    },
    tooltip: {
      trigger: "item",
      backgroundColor: "#1e293b",
      borderWidth: 0,
      textStyle: { color: "#e2e8f0" },
      formatter: (p: { seriesName: string; value: number[] | [number, number] }) => {
        const v = p.value as number[];
        if (v.length >= 4) {
          const qty = v[1] - v[0];
          return `${v[3]}<br/>${p.seriesName} · ${qty.toFixed(3)} kWh @ ${v[2].toFixed(2)} $/kWh`;
        }
        return `Demand · ${v[1].toFixed(2)} $/kWh at ${v[0].toFixed(3)} kWh`;
      },
    },
    series: [
      ...stackSeries,
      {
        name: "Demand (bids)",
        type: "line",
        data: demand,
        symbol: "none",
        lineStyle: { color: "#e2e8f0", width: 1.5, type: "dashed" },
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
