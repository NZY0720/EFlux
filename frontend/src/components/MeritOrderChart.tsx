import ReactECharts from "echarts-for-react";
import { useEffect, useMemo, useState } from "react";

import { fetchSupplyCurve } from "../api/client";
import type { SupplyCurve } from "../api/types";
import { CATEGORY_ORDER, categoryMeta } from "../lib/categories";
import { formatPrice, formatQuantity } from "../lib/format";
import { EmptyState } from "./DashboardCard";
import { chartAxis, chartLegend, chartTooltip, useChartTheme } from "./chartTheme";

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
  visual: (key: string) => unknown;
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
  const theme = useChartTheme();

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
    return <EmptyState className="h-72" title="Loading supply stack..." />;
  }
  if (curve.asks.length === 0 && curve.bids.length === 0) {
    return (
      <EmptyState
        className="h-72"
        title="Order book is empty"
        body="The stack rebuilds within seconds as agents quote."
      />
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
      style: {
        fill: api.visual("color") as string,
        stroke: theme.grid,
        lineWidth: 0.5,
        opacity: 0.86,
      },
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
      ...chartLegend(theme),
    },
    xAxis: {
      type: "value",
      name: "cumulative qty (kWh)",
      nameLocation: "middle",
      nameGap: 28,
      nameTextStyle: { color: theme.muted, fontSize: 11 },
      ...chartAxis(theme),
    },
    yAxis: {
      type: "value",
      name: "price ($/MWh)",
      nameTextStyle: { color: theme.muted, fontSize: 11 },
      ...chartAxis(theme),
    },
    tooltip: {
      trigger: "item",
      ...chartTooltip(theme),
      formatter: (p: { seriesName: string; value: number[] | [number, number] }) => {
        const v = p.value as number[];
        if (v.length >= 4) {
          const qty = v[1] - v[0];
          return `${v[3]}<br/>${p.seriesName} · ${formatQuantity(qty)} kWh @ ${formatPrice(v[2])} $/MWh`;
        }
        return `Demand · ${formatPrice(v[1])} $/MWh at ${formatQuantity(v[0])} kWh`;
      },
    },
    series: [
      ...stackSeries,
      {
        name: "Demand (bids)",
        type: "line",
        data: demand,
        symbol: "none",
        lineStyle: { color: theme.text, width: 1.6, type: "dashed" },
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
