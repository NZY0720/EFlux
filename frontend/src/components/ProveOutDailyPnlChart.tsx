import ReactECharts from "echarts-for-react";
import { useMemo } from "react";

import type { ProveOutDailyResult } from "../api/proveout";
import { EmptyState } from "./DashboardCard";
import { chartAxis, chartTooltip, useChartTheme } from "./chartTheme";

export default function ProveOutDailyPnlChart({ daily }: { daily: ProveOutDailyResult[] }) {
  const theme = useChartTheme();
  const option = useMemo(() => ({
    backgroundColor: "transparent",
    grid: { left: 58, right: 20, top: 20, bottom: 38 },
    xAxis: { type: "category" as const, data: daily.map((point) => point.date), ...chartAxis(theme) },
    yAxis: { type: "value" as const, scale: true, name: "PnL ($)", nameTextStyle: { color: theme.muted, fontSize: 11 }, ...chartAxis(theme) },
    tooltip: {
      trigger: "axis" as const,
      valueFormatter: (value: number) => `$${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}`,
      ...chartTooltip(theme),
    },
    series: [{
      type: "bar" as const,
      data: daily.map((point) => ({ value: point.pnl_usd, itemStyle: { color: point.pnl_usd >= 0 ? theme.success : theme.danger } })),
      barMaxWidth: 34,
      animation: false,
    }],
  }), [daily, theme]);

  if (!daily.length) return <EmptyState className="h-full" title="No daily results recorded" />;
  return <ReactECharts option={option} style={{ height: "100%", width: "100%" }} notMerge lazyUpdate />;
}
