import ReactECharts from "echarts-for-react";
import { useMemo } from "react";

import type { PnlPoint } from "../state/useStrategyPnl";
import { EmptyState } from "./DashboardCard";
import { chartAxis, chartLegend, chartTooltip, useChartTheme } from "./chartTheme";
import { timeZoom, usePersistentTimeZoom } from "./chartZoom";

interface Props {
  history: Record<string, PnlPoint[]>;
  /** Plot only the top-N agents by latest PnL, to keep the chart readable. */
  topN?: number;
}

const lastPnl = (pts: PnlPoint[]): number => (pts.length ? pts[pts.length - 1].pnl : 0);

/**
 * Per-strategy cumulative-PnL equity curves over time, built from the full
 * client-side PnL history (see useStrategyPnl) since the session started. A
 * draggable time-zoom slider lets you focus any window. Shows the top-N earners
 * so the legend stays legible.
 */
export default function EquityCurves({ history, topN = 8 }: Props) {
  // Preserve the zoom window (absolute time) across polling-driven rebuilds (see PriceChart).
  const { zoomRef, onEvents } = usePersistentTimeZoom();
  const theme = useChartTheme();

  const option = useMemo(() => {
    const names = Object.keys(history)
      .map((n) => ({ n, last: lastPnl(history[n]) }))
      .sort((a, b) => b.last - a.last)
      .slice(0, topN)
      .map((x) => x.n);

    const series = names.map((n) => ({
      type: "line" as const,
      name: n,
      showSymbol: false,
      smooth: false,
      sampling: "lttb",
      data: history[n].map((p) => [p.t, p.pnl]),
    }));

    return {
      backgroundColor: "transparent",
      legend: { top: 0, type: "scroll" as const, ...chartLegend(theme) },
      grid: { left: 56, right: 20, top: 32, bottom: 56 },
      xAxis: {
        type: "time" as const,
        ...chartAxis(theme),
      },
      yAxis: {
        type: "value" as const,
        scale: true,
        name: "PnL ($)",
        nameTextStyle: { color: theme.muted, fontSize: 11 },
        ...chartAxis(theme),
      },
      tooltip: { trigger: "axis" as const, ...chartTooltip(theme) },
      dataZoom: timeZoom(zoomRef.current, {
        bg: theme.surface,
        border: theme.tooltipBorder,
        filler: "rgba(34, 183, 232, 0.14)",
        handle: theme.axis,
        axis: theme.axis,
        grid: theme.grid,
        accent: theme.accent,
      }),
      series,
      animation: false,
    };
  }, [history, topN, theme, zoomRef]);

  const hasData = Object.keys(history).length > 0;

  return (
    <div className="lg-solid h-72 w-full p-1">
      {hasData ? (
        <ReactECharts option={option} style={{ height: "100%", width: "100%" }} onEvents={onEvents} notMerge lazyUpdate />
      ) : (
        <EmptyState className="h-full" title="Accumulating PnL history..." />
      )}
    </div>
  );
}
