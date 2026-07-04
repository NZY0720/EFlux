import ReactECharts from "echarts-for-react";
import { useMemo, useState } from "react";
import { Maximize2 } from "lucide-react";

import type { MarketEvent } from "../api/types";
import { chartAxis, chartLegend, chartTooltip, useChartTheme } from "./chartTheme";
import { timeZoom, usePersistentTimeZoom } from "./chartZoom";

interface PricePoint {
  ts: number; // ms
  price: number;
}

interface Candle {
  t: number; // bucket-start ms
  o: number;
  h: number;
  l: number;
  c: number;
}

interface TradeMarker {
  agentId: number;
  ts: number;
  avgPrice: number;
  qty: number;
  side: "buy" | "sell";
  color: string;
}

interface TradeFill {
  agentId: number;
  ts: number;
  price: number;
  qty: number;
  side: "buy" | "sell";
  color: string;
}

interface MarkerDatum {
  value: number[];
  symbolSize: number;
  itemStyle: { color: string };
}

interface MyAgent {
  id: number;
  name: string;
  color: string;
}

type Mode = "line" | "candles";
const INTERVALS = [
  { sec: 300, label: "5m" },
  { sec: 1800, label: "30m" },
  { sec: 3600, label: "1h" },
] as const;

interface Props {
  events: MarketEvent[];
  initialPrice?: number | null;
  initialExternalPrice?: number | null;
  /**
   * "p2p" (default): blue emergent P2P line + dashed CAISO reference, with an
   * optional OHLC candle view.
   * "realprice": the CAISO grid price is the primary (and only) line — there is
   * no P2P book in that market, so last_price never updates and candles are hidden.
   */
  variant?: "p2p" | "realprice";
  myAgents?: MyAgent[];
  hiddenAgentIds?: number[];
}

const fmtTime = (ms: number) => new Date(ms).toLocaleTimeString("en-GB", { hour12: false });

const markerSize = (qty: number) => Math.max(8, Math.min(18, 8 + Math.sqrt(Math.max(0, qty)) * 3));

/**
 * Streaming price view that retains all history since the session started and
 * exposes a draggable time-zoom axis to inspect any window:
 *  - Line: raw price over time (last_price from tick events + each trade price).
 *  - Candles (p2p only): OHLC aggregated into 5m / 30m / 1h buckets.
 *
 * Points are derived from the (already deduped) provider buffer on every render —
 * no incremental state — so route remounts rebuild full history rather than
 * wiping the chart.
 */
export default function PriceChart({
  events,
  initialPrice,
  initialExternalPrice,
  variant = "p2p",
  myAgents,
  hiddenAgentIds,
}: Props) {
  const [mode, setMode] = useState<Mode>("line");
  const [intervalSec, setIntervalSec] = useState<number>(300);
  const theme = useChartTheme();
  const allowCandles = variant === "p2p";
  const effectiveMode: Mode = allowCandles ? mode : "line";

  // Persist the user's zoom window (absolute time) across streaming rebuilds.
  // Mutated on the echarts "datazoom" event and re-applied to the option each
  // render, so a 1Hz data tick doesn't snap the view back to full range.
  const { zoomRef, onEvents, autoFollow, resetZoom, setExtent } = usePersistentTimeZoom({ trackAutoFollow: true });

  // All price points in the buffer (oldest first): trade prints + per-tick
  // last_price. Full session history — the time-zoom slider trims the view.
  const points = useMemo(() => {
    const pts: PricePoint[] = [];
    for (const e of events) {
      let price: number | null = null;
      if (e.kind === "trade") {
        price = Number(e.price);
      } else if (e.kind === "tick") {
        if (e.last_price !== null && e.last_price !== undefined) price = Number(e.last_price);
      }
      if (price !== null && Number.isFinite(price)) {
        pts.push({ ts: new Date(e.wall_ts).getTime(), price });
      }
    }
    pts.sort((a, b) => a.ts - b.ts);
    return pts;
  }, [events]);

  const externalPoints = useMemo(() => {
    const pts: PricePoint[] = [];
    for (const e of events) {
      if (e.kind !== "tick" || e.external_price === null || e.external_price === undefined) continue;
      const price = Number(e.external_price);
      if (Number.isFinite(price)) pts.push({ ts: new Date(e.wall_ts).getTime(), price });
    }
    pts.sort((a, b) => a.ts - b.ts);
    return pts;
  }, [events]);

  const linePoints = useMemo(() => {
    if (points.length === 0 && initialPrice !== null && initialPrice !== undefined) {
      return [{ ts: Date.now(), price: initialPrice }];
    }
    return points;
  }, [points, initialPrice]);

  const lineExternalPoints = useMemo(() => {
    if (externalPoints.length === 0 && initialExternalPrice !== null && initialExternalPrice !== undefined) {
      return [{ ts: Date.now(), price: initialExternalPrice }];
    }
    return externalPoints;
  }, [externalPoints, initialExternalPrice]);

  const myTradeMarkers = useMemo(() => {
    if (!myAgents?.length) return [] as TradeMarker[];
    const hidden = new Set(hiddenAgentIds ?? []);
    const visibleColors = new Map(myAgents.filter((agent) => !hidden.has(agent.id)).map((agent) => [agent.id, agent.color]));
    const fills: TradeFill[] = [];
    const addFill = (agentId: number, side: "buy" | "sell", ts: number, price: number, qty: number) => {
      const color = visibleColors.get(agentId);
      if (!color) return;
      fills.push({ agentId, ts, side, price, qty, color });
    };
    for (const e of events) {
      const ts = new Date(e.wall_ts).getTime();
      if (!Number.isFinite(ts)) continue;
      if (e.kind === "trade") {
        const price = Number(e.price);
        const qty = Number(e.qty);
        if (!Number.isFinite(price) || !Number.isFinite(qty)) continue;
        addFill(e.buy_vpp_id, "buy", ts, price, qty);
        addFill(e.sell_vpp_id, "sell", ts, price, qty);
      } else if (e.kind === "external.trade" && (e.side === "buy" || e.side === "sell")) {
        const price = Number(e.price);
        const qty = Number(e.qty);
        if (!Number.isFinite(price) || !Number.isFinite(qty)) continue;
        addFill(e.vpp_id, e.side, ts, price, qty);
      }
    }
    const grouped = new Map<string, { fill: TradeFill; sumQty: number; weightedPrice: number; prices: number[] }>();
    for (const fill of fills) {
      const key = `${fill.agentId}:${fill.ts}:${fill.side}`;
      const existing = grouped.get(key);
      if (existing) {
        existing.sumQty += fill.qty;
        existing.weightedPrice += fill.price * fill.qty;
        existing.prices.push(fill.price);
      } else {
        grouped.set(key, {
          fill,
          sumQty: fill.qty,
          weightedPrice: fill.price * fill.qty,
          prices: [fill.price],
        });
      }
    }
    const markers = [...grouped.values()].map(({ fill, sumQty, weightedPrice, prices }) => {
      const avgPrice = sumQty > 0 ? weightedPrice / sumQty : prices.reduce((acc, price) => acc + price, 0) / prices.length;
      return { agentId: fill.agentId, ts: fill.ts, side: fill.side, avgPrice, qty: sumQty, color: fill.color };
    });
    markers.sort((a, b) => a.ts - b.ts || a.agentId - b.agentId || a.side.localeCompare(b.side));
    return markers;
  }, [events, hiddenAgentIds, myAgents]);

  // Which series the candles/line treat as primary. The real-price market has no
  // P2P book, so its primary price is the CAISO (external) feed.
  const primaryPoints = variant === "realprice" ? externalPoints : points;

  // OHLC buckets. Points are sorted ascending, so the first in a bucket is the
  // open and the last is the close. Keep the full session; the zoom slider trims.
  const candles = useMemo(() => {
    if (effectiveMode !== "candles") return [] as Candle[];
    const bucketMs = intervalSec * 1000;
    const byBucket = new Map<number, Candle>();
    for (const p of primaryPoints) {
      const t = Math.floor(p.ts / bucketMs) * bucketMs;
      const cur = byBucket.get(t);
      if (!cur) {
        byBucket.set(t, { t, o: p.price, h: p.price, l: p.price, c: p.price });
      } else {
        cur.h = Math.max(cur.h, p.price);
        cur.l = Math.min(cur.l, p.price);
        cur.c = p.price;
      }
    }
    return [...byBucket.values()].sort((a, b) => a.t - b.t);
  }, [primaryPoints, effectiveMode, intervalSec]);

  const displayedLinePoints = variant === "realprice" ? lineExternalPoints : linePoints;
  if (effectiveMode === "candles") {
    setExtent(0, Math.max(0, candles.length - 1));
  } else if (displayedLinePoints.length > 0) {
    setExtent(displayedLinePoints[0].ts, displayedLinePoints[displayedLinePoints.length - 1].ts);
  } else {
    setExtent(0, 0);
  }

  const lineBuyMarkerData = myTradeMarkers
    .filter((m) => m.side === "buy")
    .map((m) => ({ value: [m.ts, m.avgPrice], symbolSize: markerSize(m.qty), itemStyle: { color: m.color } }));
  const lineSellMarkerData = myTradeMarkers
    .filter((m) => m.side === "sell")
    .map((m) => ({ value: [m.ts, m.avgPrice], symbolSize: markerSize(m.qty), itemStyle: { color: m.color } }));

  const candleBucketIndex = new Map(candles.map((c, idx) => [c.t, idx]));
  const bucketMs = intervalSec * 1000;
  const candleBuyMarkerData = myTradeMarkers
    .filter((m) => m.side === "buy")
    .map((m) => {
      const idx = candleBucketIndex.get(Math.floor(m.ts / bucketMs) * bucketMs);
      return idx === undefined ? null : { value: [idx, m.avgPrice], symbolSize: markerSize(m.qty), itemStyle: { color: m.color } };
    })
    .filter((m): m is MarkerDatum => m !== null);
  const candleSellMarkerData = myTradeMarkers
    .filter((m) => m.side === "sell")
    .map((m) => {
      const idx = candleBucketIndex.get(Math.floor(m.ts / bucketMs) * bucketMs);
      return idx === undefined ? null : { value: [idx, m.avgPrice], symbolSize: markerSize(m.qty), itemStyle: { color: m.color } };
    })
    .filter((m): m is MarkerDatum => m !== null);

  const buyScatterSeries = {
    type: "scatter" as const,
    name: "My buys",
    symbol: "triangle",
    data: effectiveMode === "candles" ? candleBuyMarkerData : lineBuyMarkerData,
    z: 10,
  };

  const sellScatterSeries = {
    type: "scatter" as const,
    name: "My sells",
    symbol: "triangle",
    symbolRotate: 180,
    data: effectiveMode === "candles" ? candleSellMarkerData : lineSellMarkerData,
    z: 10,
  };
  const markerSeries = myAgents !== undefined ? [buyScatterSeries, sellScatterSeries] : [];

  const baseAxis = chartAxis(theme);
  const zoomTheme = {
    bg: theme.surface,
    border: theme.tooltipBorder,
    filler: "rgba(34, 183, 232, 0.14)",
    handle: theme.axis,
    axis: theme.axis,
    grid: theme.grid,
    accent: theme.accent,
  };

  const lineOption = {
    backgroundColor: "transparent",
    legend: { top: 0, right: 12, ...chartLegend(theme), data: [variant === "realprice" ? "CAISO (grid price)" : "P2P"] },
    grid: { left: 50, right: 20, top: 32, bottom: 56 },
    xAxis: { type: "time", ...baseAxis },
    yAxis: {
      type: "value",
      scale: true,
      name: "price ($/MWh)",
      nameTextStyle: { color: theme.muted, fontSize: 11 },
      ...baseAxis,
    },
    tooltip: { trigger: "axis", ...chartTooltip(theme) },
    dataZoom: timeZoom(zoomRef.current, zoomTheme),
    series: [
      ...(variant === "realprice"
        ? [
            // Real-price market: the live CAISO price IS the market — make it primary.
            {
              type: "line",
              name: "CAISO (grid price)",
              showSymbol: false,
              smooth: false,
              sampling: "lttb",
              data: lineExternalPoints.map((p) => [p.ts, p.price]),
              lineStyle: { color: theme.warning, width: 1.8 },
              areaStyle: { color: "rgba(245, 158, 11, 0.12)" },
            },
          ]
        : [
            // Pure P2P: only the emergent local price. CAISO is not shown in this market.
            {
              type: "line",
              name: "P2P",
              showSymbol: false,
              smooth: false,
              sampling: "lttb",
              data: linePoints.map((p) => [p.ts, p.price]),
              lineStyle: { color: theme.accent, width: 1.8 },
              areaStyle: { color: "rgba(34, 183, 232, 0.12)" },
            },
          ]),
      ...markerSeries,
    ],
    animation: false,
  };

  const candleOption = {
    backgroundColor: "transparent",
    legend: { top: 0, right: 12, ...chartLegend(theme), data: ["P2P candles"] },
    grid: { left: 50, right: 20, top: 32, bottom: 64 },
    xAxis: {
      type: "category",
      data: candles.map((c) => fmtTime(c.t)),
      boundaryGap: true,
      ...baseAxis,
      axisLabel: { color: theme.axis, hideOverlap: true },
    },
    yAxis: {
      type: "value",
      scale: true,
      name: "price ($/MWh)",
      nameTextStyle: { color: theme.muted, fontSize: 11 },
      ...baseAxis,
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross" },
      ...chartTooltip(theme),
    },
    dataZoom: timeZoom(zoomRef.current, zoomTheme),
    series: [
      {
        type: "candlestick",
        name: "P2P candles",
        // ECharts candlestick value order: [open, close, low, high].
        data: candles.map((c) => [c.o, c.c, c.l, c.h]),
        itemStyle: {
          color: theme.success, // bullish body (close >= open)
          color0: theme.danger, // bearish body
          borderColor: theme.success,
          borderColor0: theme.danger,
        },
      },
      ...markerSeries,
    ],
    animation: false,
  };

  const hasCandles = candles.length > 0;

  const segBtn = (active: boolean) =>
    `px-2.5 py-1 text-xs font-medium transition-colors ${
      active ? "bg-[var(--accent-strong)] text-[var(--accent-contrast)]" : "text-[var(--text-muted)] hover:bg-[var(--surface-hover)] hover:text-[var(--text)]"
    }`;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-end gap-2">
        <button
          type="button"
          onClick={resetZoom}
          title="Restore auto-follow (live view)"
          className={`inline-flex h-7 items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${
            autoFollow
              ? "border-[var(--border)] text-[var(--text-muted)] hover:bg-[var(--surface-hover)] hover:text-[var(--text)]"
              : "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent)] hover:bg-[var(--surface-hover)]"
          }`}
        >
          <Maximize2 size={13} />
          Auto Zoom
        </button>
        {allowCandles && (
          <>
            {effectiveMode === "candles" && (
              <div className="inline-flex overflow-hidden rounded-md border border-[var(--border)] bg-[var(--surface-inset)]">
                {INTERVALS.map((iv) => (
                  <button
                    key={iv.sec}
                    type="button"
                    onClick={() => setIntervalSec(iv.sec)}
                    className={segBtn(intervalSec === iv.sec)}
                  >
                    {iv.label}
                  </button>
                ))}
              </div>
            )}
            <div className="inline-flex overflow-hidden rounded-md border border-[var(--border)] bg-[var(--surface-inset)]">
              <button
                type="button"
                onClick={() => {
                  setMode("line");
                  resetZoom(); // line uses a time axis; reset the category-axis zoom
                }}
                className={segBtn(effectiveMode === "line")}
              >
                Line
              </button>
              <button
                type="button"
                onClick={() => {
                  setMode("candles");
                  resetZoom(); // candles use a category axis; reset the time-axis zoom
                }}
                className={segBtn(effectiveMode === "candles")}
              >
                Candles
              </button>
            </div>
          </>
        )}
      </div>
      <div className="h-72 w-full">
        {effectiveMode === "candles" && !hasCandles ? (
          <div className="flex h-full items-center justify-center text-sm text-[var(--text-subtle)]">
            Waiting for trades to aggregate…
          </div>
        ) : (
          <ReactECharts
            key={`${variant}-${effectiveMode}`}
            option={effectiveMode === "candles" ? candleOption : lineOption}
            style={{ height: "100%", width: "100%" }}
            onEvents={onEvents}
            notMerge={false}
            lazyUpdate
          />
        )}
      </div>
    </div>
  );
}
