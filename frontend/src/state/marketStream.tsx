import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

import { fetchParticipants, fetchRecentTrades, fetchSnapshot } from "../api/client";
import type { MarketEvent, MarketSnapshot } from "../api/types";
import { useMarketStream } from "../ws/useMarketStream";
import type { ConnectionState } from "../ws/useMarketStream";

const MAX_BUFFER = 1000;

interface MarketStreamValue {
  /** WS connection state. */
  state: ConnectionState;
  /** Recent events, newest first, deduped. Survives route changes — the
   * provider lives at the App level and backfills trades from the backend. */
  recent: MarketEvent[];
  /** Order-book snapshot, polled at 1Hz (WS pushes per-tick summary but not depth). */
  snapshot: MarketSnapshot | null;
  /** Human-readable VPP name for a trade-tape party (falls back to "VPP <id>"). */
  nameOf: (vppId: number) => string;
}

const Ctx = createContext<MarketStreamValue>({
  state: "connecting",
  recent: [],
  snapshot: null,
  nameOf: (id) => `VPP ${id}`,
});

function keyOf(e: MarketEvent): string {
  switch (e.kind) {
    case "trade":
      return `trade-${e.trade_id}`;
    case "tick":
      return `tick-${e.tick_no}`;
    default:
      return `${e.kind}-${e.order_id}-${e.wall_ts}`;
  }
}

export function MarketStreamProvider({ children }: { children: React.ReactNode }) {
  const [recent, setRecent] = useState<MarketEvent[]>([]);
  const [snapshot, setSnapshot] = useState<MarketSnapshot | null>(null);
  const seenRef = useRef(new Set<string>());
  const bufRef = useRef<MarketEvent[]>([]);

  // Ingest a batch (newest first), dropping anything already seen. Defense in
  // depth: even if a transport bug ever double-delivers, the buffer stays clean.
  // NOTE: dedup happens *outside* the setState updater — updaters must be pure
  // (StrictMode double-invokes them), and mutating seenRef inside one made the
  // second invocation discard every event.
  const ingest = useCallback((events: MarketEvent[]) => {
    const fresh = events.filter((e) => {
      const k = keyOf(e);
      if (seenRef.current.has(k)) return false;
      seenRef.current.add(k);
      return true;
    });
    if (fresh.length === 0) return;
    bufRef.current = [...fresh, ...bufRef.current].slice(0, MAX_BUFFER);
    if (seenRef.current.size > MAX_BUFFER * 5) {
      seenRef.current = new Set(bufRef.current.map(keyOf));
    }
    setRecent(bufRef.current);
  }, []);

  const { state } = useMarketStream({ onEvent: (e) => ingest([e]) });

  // Backfill recent trades on mount and after every reconnect, so a fresh page
  // (or a route remount) starts with history instead of an empty chart.
  useEffect(() => {
    if (state !== "open") return;
    let cancelled = false;
    fetchRecentTrades(200)
      .then((trades) => {
        if (!cancelled) ingest([...trades].reverse()); // API is oldest-first
      })
      .catch(() => {
        /* backend not ready — WS events still flow */
      });
    return () => {
      cancelled = true;
    };
  }, [state, ingest]);

  // 1Hz snapshot poll for order-book depth / KPIs / data-source banner.
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const s = await fetchSnapshot(10);
        if (!cancelled) setSnapshot(s);
      } catch {
        /* connection issues are visible via the WS indicator */
      }
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  // VPP id → name directory for labeling trade parties. Refreshed once a
  // minute to pick up newly created external VPPs.
  const [names, setNames] = useState<Record<number, string>>({});
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const parts = await fetchParticipants();
        if (!cancelled) setNames(Object.fromEntries(parts.map((p) => [p.id, p.name])));
      } catch {
        /* names fall back to raw ids */
      }
    };
    load();
    const id = setInterval(load, 60_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);
  const nameOf = useCallback((vppId: number) => names[vppId] ?? `VPP ${vppId}`, [names]);

  return <Ctx.Provider value={{ state, recent, snapshot, nameOf }}>{children}</Ctx.Provider>;
}

export function useMarket(): MarketStreamValue {
  return useContext(Ctx);
}
