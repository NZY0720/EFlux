// Shared draggable time-zoom (dataZoom) config for the streaming charts.
//
// The window is persisted as ABSOLUTE time bounds (startValue/endValue in ms),
// NOT percentages. ECharts percent bounds are relative to the data extent, which
// grows every tick — so a fixed percent window would silently widen/slide as new
// data streams in. Absolute bounds keep a zoomed window pinned to real time;
// null bounds mean "full range" while history is shorter than the auto window;
// once it grows past one hour, auto-follow uses an absolute latest-hour window.

import { useCallback, useMemo, useRef, useState, type MutableRefObject } from "react";

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
export const DEFAULT_AUTO_WINDOW_MS = 60 * 60 * 1000;

interface ZoomExtent {
  min: number;
  max: number;
}

interface ZoomTheme {
  bg?: string;
  border?: string;
  filler?: string;
  handle?: string;
  axis?: string;
  grid?: string;
  accent?: string;
}

const finiteNumber = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** Read a window from an echarts "datazoom" event, or null if it carries no usable bounds. */
export function readZoomEvent(params: ZoomEventParam, extent?: ZoomExtent | null): ZoomWindow | null {
  const z = params?.batch?.[0] ?? params;
  if (!z) return null;
  const hasExtentParam = extent !== undefined;
  // Zoomed fully out → resume auto-follow (full range). Kept only for the
  // historical no-extent path used by existing charts.
  if (!hasExtentParam && z.start === 0 && z.end === 100) return { startValue: null, endValue: null };
  if (finiteNumber(z.startValue) && finiteNumber(z.endValue)) {
    return { startValue: Math.round(z.startValue), endValue: Math.round(z.endValue) };
  }
  if (extent && finiteNumber(z.start) && finiteNumber(z.end)) {
    const span = extent.max - extent.min;
    if (Number.isFinite(span) && span >= 0) {
      return {
        startValue: Math.round(extent.min + (z.start / 100) * span),
        endValue: Math.round(extent.min + (z.end / 100) * span),
      };
    }
  }
  return null;
}

/** dataZoom (scroll/drag "inside" + styled slider) honoring an absolute-time window. */
export function timeZoom(z: ZoomWindow, theme: ZoomTheme = {}) {
  const v: { start?: number; end?: number; startValue?: number; endValue?: number } =
    z.startValue !== null && z.endValue !== null
      ? { startValue: z.startValue, endValue: z.endValue }
      : { start: 0, end: 100 };
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

interface PersistentZoomBase {
  zoomRef: MutableRefObject<ZoomWindow>;
  onEvents: {
    datazoom: (params: ZoomEventParam) => void;
  };
}

interface PersistentZoomTracked extends PersistentZoomBase {
  autoFollow: boolean;
  resetZoom: () => void;
  setExtent: (min: number, max: number, autoWindowSize?: number) => void;
}

interface PersistentZoomOptions {
  trackAutoFollow?: boolean;
}

export function usePersistentTimeZoom(): PersistentZoomBase;
export function usePersistentTimeZoom(opts: { trackAutoFollow?: false }): PersistentZoomBase;
export function usePersistentTimeZoom(opts: { trackAutoFollow: true }): PersistentZoomTracked;
/** Preserve an absolute-time zoom window across streaming chart option rebuilds. */
export function usePersistentTimeZoom(opts: PersistentZoomOptions = {}): PersistentZoomBase | PersistentZoomTracked {
  const zoomRef = useRef<ZoomWindow>(FULL_ZOOM);
  const extentRef = useRef<ZoomExtent | null>(null);
  const autoWindowSizeRef = useRef(DEFAULT_AUTO_WINDOW_MS);
  const [autoFollow, setAutoFollowState] = useState(true);
  const autoFollowRef = useRef(true);
  const trackAutoFollow = opts.trackAutoFollow === true;

  const setAutoFollow = useCallback((next: boolean) => {
    autoFollowRef.current = next;
    setAutoFollowState(next);
  }, []);

  const resetZoom = useCallback(() => {
    const extent = extentRef.current;
    zoomRef.current = extent
      ? autoZoomWindow(extent.min, extent.max, autoWindowSizeRef.current)
      : FULL_ZOOM;
    setAutoFollow(true);
  }, [setAutoFollow]);

  const setExtent = useCallback((min: number, max: number, autoWindowSize = DEFAULT_AUTO_WINDOW_MS) => {
    if (Number.isFinite(min) && Number.isFinite(max)) {
      extentRef.current = { min, max };
      autoWindowSizeRef.current = autoWindowSize;
      if (autoFollowRef.current) {
        zoomRef.current = autoZoomWindow(min, max, autoWindowSize);
      }
    }
  }, []);

  const onEvents = useMemo(
    () => ({
      datazoom: (params: ZoomEventParam) => {
        const w = readZoomEvent(params, trackAutoFollow ? extentRef.current : undefined);
        if (!w) return;
        zoomRef.current = w;
        if (trackAutoFollow && autoFollowRef.current) setAutoFollow(false);
      },
    }),
    [setAutoFollow, trackAutoFollow],
  );
  if (!trackAutoFollow) return { zoomRef, onEvents };
  return { zoomRef, onEvents, autoFollow, resetZoom, setExtent };
}

/** Full range until it exceeds the desired window, then follow the newest slice. */
export function autoZoomWindow(min: number, max: number, windowSize = DEFAULT_AUTO_WINDOW_MS): ZoomWindow {
  const span = max - min;
  if (!Number.isFinite(span) || span <= Math.max(0, windowSize)) return FULL_ZOOM;
  return { startValue: max - windowSize, endValue: max };
}
