import { useMemo } from "react";

import { useTheme } from "../state/theme";

export interface ChartTheme {
  axis: string;
  grid: string;
  text: string;
  muted: string;
  surface: string;
  tooltipBorder: string;
  accent: string;
  success: string;
  warning: string;
  danger: string;
  violet: string;
}

const fallback: ChartTheme = {
  axis: "#94a3b8",
  grid: "rgba(148, 163, 184, 0.16)",
  text: "#e2e8f0",
  muted: "#64748b",
  surface: "#111827",
  tooltipBorder: "rgba(148, 163, 184, 0.22)",
  accent: "#38bdf8",
  success: "#10b981",
  warning: "#f59e0b",
  danger: "#f43f5e",
  violet: "#a78bfa",
};

function cssVar(name: string, value: string): string {
  const root = getComputedStyle(document.documentElement);
  return root.getPropertyValue(name).trim() || value;
}

export function useChartTheme(): ChartTheme {
  const { mode } = useTheme();
  return useMemo(
    () => ({
      axis: cssVar("--chart-axis", fallback.axis),
      grid: cssVar("--chart-grid", fallback.grid),
      text: cssVar("--text", fallback.text),
      muted: cssVar("--text-subtle", fallback.muted),
      surface: cssVar("--chart-tooltip-bg", fallback.surface),
      tooltipBorder: cssVar("--chart-tooltip-border", fallback.tooltipBorder),
      accent: cssVar("--accent", fallback.accent),
      success: cssVar("--success", fallback.success),
      warning: cssVar("--warning", fallback.warning),
      danger: cssVar("--danger", fallback.danger),
      violet: cssVar("--violet", fallback.violet),
    }),
    [mode],
  );
}

export function chartAxis(theme: ChartTheme) {
  return {
    axisLine: { lineStyle: { color: theme.grid } },
    axisTick: { lineStyle: { color: theme.grid } },
    axisLabel: { color: theme.axis },
    splitLine: { lineStyle: { color: theme.grid } },
  };
}

export function chartTooltip(theme: ChartTheme) {
  return {
    backgroundColor: theme.surface,
    borderColor: theme.tooltipBorder,
    borderWidth: 1,
    textStyle: { color: theme.text },
    extraCssText: "box-shadow: 0 18px 42px -28px rgba(15,23,42,.62); border-radius: 8px;",
  };
}

export function chartLegend(theme: ChartTheme) {
  return {
    textStyle: { color: theme.axis, fontSize: 11 },
    itemWidth: 12,
    itemHeight: 8,
  };
}
