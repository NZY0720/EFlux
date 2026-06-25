import ReactECharts from "echarts-for-react";
import { useMemo, useRef } from "react";

import type { PnlPoint } from "../state/useStrategyPnl";
import { FULL_ZOOM, readZoomEvent, timeZoom, type ZoomWindow } from "./chartZoom";

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
  const zoomRef = useRef<ZoomWindow>(FULL_ZOOM);
  const onEvents = useMemo(
    () => ({
      datazoom: (params: {
        start?: number;
        end?: number;
        startValue?: number;
        endValue?: number;
        batch?: Array<{ start?: number; end?: number; startValue?: number; endValue?: number }>;
      }) => {
        const w = readZoomEvent(params);
        if (w) zoomRef.current = w;
      },
    }),
    [],
  );

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
      legend: { top: 0, type: "scroll" as const, textStyle: { color: "#94a3b8" } },
      grid: { left: 56, right: 20, top: 32, bottom: 56 },
      xAxis: {
        type: "time" as const,
        axisLabel: { color: "#94a3b8" },
        splitLine: { lineStyle: { color: "#1e293b" } },
      },
      yAxis: {
        type: "value" as const,
        scale: true,
        name: "PnL ($)",
        nameTextStyle: { color: "#64748b", fontSize: 11 },
        axisLabel: { color: "#94a3b8" },
        splitLine: { lineStyle: { color: "#1e293b" } },
      },
      tooltip: { trigger: "axis" as const, backgroundColor: "#1e293b", borderWidth: 0, textStyle: { color: "#e2e8f0" } },
      dataZoom: timeZoom(zoomRef.current),
      series,
      animation: false,
    };
  }, [history, topN]);

  const hasData = Object.keys(history).length > 0;

  return (
    <div className="h-72 w-full">
      {hasData ? (
        <ReactECharts option={option} style={{ height: "100%", width: "100%" }} onEvents={onEvents} notMerge lazyUpdate />
      ) : (
        <div className="flex h-full items-center justify-center text-sm text-slate-500">
          Accumulating PnL history…
        </div>
      )}
    </div>
  );
}
