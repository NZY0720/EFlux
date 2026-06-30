import ReactECharts from "echarts-for-react";

import type { MarketSnapshot } from "../api/types";
import { EmptyState } from "./DashboardCard";
import { chartAxis, chartLegend, chartTooltip, useChartTheme } from "./chartTheme";

interface Props {
  snapshot: MarketSnapshot | null;
}

/**
 * Bid/ask depth: each side a stepped area, x = price, y = cumulative qty.
 */
export default function OrderBookDepth({ snapshot }: Props) {
  const theme = useChartTheme();
  if (!snapshot) {
    return <EmptyState className="h-72" title="Loading order book..." />;
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
    legend: { top: 0, ...chartLegend(theme) },
    xAxis: {
      type: "value",
      scale: true,
      name: "price ($/MWh)",
      nameLocation: "middle",
      nameGap: 28,
      nameTextStyle: { color: theme.muted, fontSize: 11 },
      ...chartAxis(theme),
    },
    yAxis: {
      type: "value",
      name: "cumulative qty (kWh)",
      nameTextStyle: { color: theme.muted, fontSize: 11 },
      ...chartAxis(theme),
    },
    tooltip: { trigger: "axis", ...chartTooltip(theme) },
    series: [
      {
        name: "Bids",
        type: "line",
        step: "end",
        data: bidData,
        lineStyle: { color: theme.success, width: 1.7 },
        areaStyle: { color: "rgba(16, 185, 129, 0.2)" },
        symbol: "none",
      },
      {
        name: "Asks",
        type: "line",
        step: "start",
        data: askData,
        lineStyle: { color: theme.danger, width: 1.7 },
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
