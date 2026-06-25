// Shared draggable time-zoom (dataZoom) config for the streaming charts.
//
// The window is persisted as ABSOLUTE time bounds (startValue/endValue in ms),
// NOT percentages. ECharts percent bounds are relative to the data extent, which
// grows every tick — so a fixed percent window would silently widen/slide as new
// data streams in. Absolute bounds keep a zoomed window pinned to real time;
// null bounds mean "full range" (auto-follow new data).

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
export function timeZoom(z: ZoomWindow) {
  const v: { startValue?: number; endValue?: number } =
    z.startValue !== null && z.endValue !== null ? { startValue: z.startValue, endValue: z.endValue } : {};
  return [
    { type: "inside" as const, filterMode: "filter" as const, ...v },
    {
      type: "slider" as const,
      filterMode: "filter" as const,
      ...v,
      bottom: 6,
      height: 16,
      backgroundColor: "#0f172a",
      borderColor: "#1e293b",
      fillerColor: "rgba(56, 189, 248, 0.12)",
      handleStyle: { color: "#475569" },
      moveHandleStyle: { color: "#475569" },
      dataBackground: { lineStyle: { color: "#334155" }, areaStyle: { color: "#1e293b" } },
      selectedDataBackground: { lineStyle: { color: "#38bdf8" }, areaStyle: { color: "rgba(56, 189, 248, 0.15)" } },
      textStyle: { color: "#64748b" },
    },
  ];
}
