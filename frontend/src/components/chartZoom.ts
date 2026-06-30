// Shared draggable time-zoom (dataZoom) config for the streaming charts.
//
// The window is persisted as ABSOLUTE time bounds (startValue/endValue in ms),
// NOT percentages. ECharts percent bounds are relative to the data extent, which
// grows every tick — so a fixed percent window would silently widen/slide as new
// data streams in. Absolute bounds keep a zoomed window pinned to real time;
// null bounds mean "full range" (auto-follow new data).

import { useMemo, useRef } from "react";

export interface ZoomWindow {
  startValue: number | null;
  endValue: number | null;
}

interface ZoomEventParam {
  start?: number;
  end?: number;
  startValue?: number;
  endValue?: number;
  batch?: Array<{ start?: number; end?: number; startValue?: number; endValue?: number }>;
}

export const FULL_ZOOM: ZoomWindow = { startValue: null, endValue: null };

interface ZoomTheme {
  bg?: string;
  border?: string;
  filler?: string;
  handle?: string;
  axis?: string;
  grid?: string;
  accent?: string;
}

/** Read a window from an echarts "datazoom" event, or null if it carries no usable bounds. */
export function readZoomEvent(params: ZoomEventParam): ZoomWindow | null {
  const z = params?.batch?.[0] ?? params;
  if (!z) return null;
  // Zoomed fully out → resume auto-follow (full range).
  if (z.start === 0 && z.end === 100) return { startValue: null, endValue: null };
  if (typeof z.startValue === "number" && typeof z.endValue === "number") {
    return { startValue: Math.round(z.startValue), endValue: Math.round(z.endValue) };
  }
  return null;
}

/** dataZoom (scroll/drag "inside" + styled slider) honoring an absolute-time window. */
export function timeZoom(z: ZoomWindow, theme: ZoomTheme = {}) {
  const v: { startValue?: number; endValue?: number } =
    z.startValue !== null && z.endValue !== null ? { startValue: z.startValue, endValue: z.endValue } : {};
  const accent = theme.accent ?? "#38bdf8";
  return [
    { type: "inside" as const, filterMode: "filter" as const, ...v },
    {
      type: "slider" as const,
      filterMode: "filter" as const,
      ...v,
      bottom: 6,
      height: 16,
      backgroundColor: theme.bg ?? "#0f172a",
      borderColor: theme.border ?? "#1e293b",
      fillerColor: theme.filler ?? "rgba(56, 189, 248, 0.12)",
      handleStyle: { color: theme.handle ?? "#475569" },
      moveHandleStyle: { color: theme.handle ?? "#475569" },
      dataBackground: { lineStyle: { color: theme.grid ?? "#334155" }, areaStyle: { color: theme.border ?? "#1e293b" } },
      selectedDataBackground: { lineStyle: { color: accent }, areaStyle: { color: theme.filler ?? "rgba(56, 189, 248, 0.15)" } },
      textStyle: { color: theme.axis ?? "#64748b" },
    },
  ];
}

/** Preserve an absolute-time zoom window across streaming chart option rebuilds. */
export function usePersistentTimeZoom() {
  const zoomRef = useRef<ZoomWindow>(FULL_ZOOM);
  const onEvents = useMemo(
    () => ({
      datazoom: (params: ZoomEventParam) => {
        const w = readZoomEvent(params);
        if (w) zoomRef.current = w;
      },
    }),
    [],
  );
  return { zoomRef, onEvents };
}
