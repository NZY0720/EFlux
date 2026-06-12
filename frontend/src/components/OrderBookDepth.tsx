import ReactECharts from "echarts-for-react";

import type { MarketSnapshot } from "../api/types";

interface Props {
  snapshot: MarketSnapshot | null;
}

/**
 * Bid/ask depth: each side a stepped area, x = price, y = cumulative qty.
 */
export default function OrderBookDepth({ snapshot }: Props) {
  if (!snapshot) {
    return <div className="h-72 flex items-center justify-center text-slate-500">Loading order book…</div>;
  }

  const cumulative = (levels: [string, string][], side: "buy" | "sell") => {
    // bids are sorted by best (highest) first; reverse to ascending price for plotting.
    const sorted = [...levels].map(([p, q]) => [Number(p), Number(q)] as [number, number]);
    sorted.sort((a, b) => a[0] - b[0]);
    let cum = 0;
    const pts: [number, number][] = [];
    if (side === "buy") {
      // for bids, accumulate from highest to lowest, so we walk reversed:
      const rev = sorted.slice().reverse();
      for (const [p, q] of rev) {
        cum += q;
        pts.push([p, cum]);
      }
      pts.reverse();
    } else {
      for (const [p, q] of sorted) {
        cum += q;
        pts.push([p, cum]);
      }
    }
    return pts;
  };

  const bidData = cumulative(snapshot.bids, "buy");
  const askData = cumulative(snapshot.asks, "sell");

  const option = {
    backgroundColor: "transparent",
    grid: { left: 55, right: 20, top: 30, bottom: 42 },
    legend: { top: 0, textStyle: { color: "#94a3b8", fontSize: 11 }, itemWidth: 12, itemHeight: 8 },
    xAxis: {
      type: "value",
      scale: true,
      name: "price ($/kWh)",
      nameLocation: "middle",
      nameGap: 28,
      nameTextStyle: { color: "#64748b", fontSize: 11 },
      axisLabel: { color: "#94a3b8" },
      splitLine: { lineStyle: { color: "#1e293b" } },
    },
    yAxis: {
      type: "value",
      name: "cumulative qty (kWh)",
      nameTextStyle: { color: "#64748b", fontSize: 11 },
      axisLabel: { color: "#94a3b8" },
      splitLine: { lineStyle: { color: "#1e293b" } },
    },
    tooltip: { trigger: "axis", backgroundColor: "#1e293b", borderWidth: 0, textStyle: { color: "#e2e8f0" } },
    series: [
      {
        name: "Bids",
        type: "line",
        step: "end",
        data: bidData,
        lineStyle: { color: "#10b981" },
        areaStyle: { color: "rgba(16, 185, 129, 0.2)" },
        symbol: "none",
      },
      {
        name: "Asks",
        type: "line",
        step: "start",
        data: askData,
        lineStyle: { color: "#f43f5e" },
        areaStyle: { color: "rgba(244, 63, 94, 0.2)" },
        symbol: "none",
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
