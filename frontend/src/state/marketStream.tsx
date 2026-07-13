import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

import { fetchParticipants, fetchRecentTicks, fetchRecentTrades, fetchSnapshot } from "../api/client";
import type { MarketEvent, MarketSnapshot } from "../api/types";
import { useMarketStream } from "../ws/useMarketStream";
import type { ConnectionState } from "../ws/useMarketStream";

// Retain a long event history so the price chart can zoom back to the session
// start. The buffer holds ticks/trades (and, in p2p, order events); bounded only
// as a memory safety net — the time-zoom slider navigates within it.
const MAX_TICK_HISTORY = 100_000;
const MAX_BUFFER = MAX_TICK_HISTORY + 500;

interface MarketStreamValue {
  /** WS connection state. */
  state: ConnectionState;
  /** Current-session events, newest first, deduped. Survives route changes and
   * backfills retained ticks/trades from the backend after a hard refresh. */
  recent: MarketEvent[];
  /** Order-book snapshot, polled at 1Hz (WS pushes per-tick summary but not depth). */
  snapshot: MarketSnapshot | null;
  /** True when no snapshot has succeeded for >5s — charts may be showing old data. */
  stale: boolean;
  /** Wall-clock ms of the last detected backend restart (tick numbering reset), or null. */
  restartedAt: number | null;
  /** Human-readable VPP name for a trade-tape party (falls back to "VPP <id>"). */
  nameOf: (vppId: number) => string;
}

const Ctx = createContext<MarketStreamValue>({
  state: "connecting",
  recent: [],
  snapshot: null,
  stale: false,
  restartedAt: null,
  nameOf: (id) => `VPP ${id}`,
});

function keyOf(e: MarketEvent): string {
  switch (e.kind) {
    case "trade":
      return `trade-${e.trade_id}`;
    case "external.trade":
      return `external-trade-${e.external_trade_id}`;
    case "tick":
      return `tick-${e.tick_no}`;
    default:
      return `${e.kind}-${e.order_id}-${e.wall_ts}`;
  }
}

export function MarketStreamProvider({ children }: { children: React.ReactNode }) {
  const [recent, setRecent] = useState<MarketEvent[]>([]);
  const [snapshot, setSnapshot] = useState<MarketSnapshot | null>(null);
  const [stale, setStale] = useState(false);
  const [restartedAt, setRestartedAt] = useState<number | null>(null);
  const seenRef = useRef(new Set<string>());
  const bufRef = useRef<MarketEvent[]>([]);
  const lastTickNoRef = useRef(0);
  const lastSnapshotOkRef = useRef(Date.now());

  // Ingest a batch (newest first), dropping anything already seen. Defense in
  // depth: even if a transport bug ever double-delivers, the buffer stays clean.
  // NOTE: dedup happens *outside* the setState updater — updaters must be pure
  // (StrictMode double-invokes them), and mutating seenRef inside one made the
  // second invocation discard every event.
  const ingest = useCallback((events: MarketEvent[]) => {
    // Backend restart detection: the in-memory market numbers ticks from 1, so
    // a tick_no far below the last seen one means a fresh run. Reset the dedup
    // set and buffer — the new run reuses tick/trade ids, and stale keys would
    // silently swallow every event it emits.
    for (const e of events) {
      if (e.kind !== "tick") continue;
      if (e.tick_no < lastTickNoRef.current - 1) {
        seenRef.current = new Set();
        bufRef.current = [];
        setRestartedAt(Date.now());
      }
      lastTickNoRef.current = e.tick_no;
    }
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

  // Backfill the current session on mount and reconnect. Ticks restore both the
  // P2P and CAISO price curves after a hard refresh; trades restore the tape and
  // agent markers. Both APIs return oldest-first, while ingest expects newest-first.
  useEffect(() => {
    if (state !== "open") return;
    let cancelled = false;
    Promise.all([fetchRecentTicks(MAX_TICK_HISTORY), fetchRecentTrades(500)])
      .then(([ticks, trades]) => {
        if (cancelled) return;
        const history = [...ticks, ...trades].sort(
          (a, b) => new Date(b.wall_ts).getTime() - new Date(a.wall_ts).getTime(),
        );
        ingest(history);
      })
      .catch(() => {
        /* backend not ready or history unavailable — live WS events still flow */
      });
    return () => {
      cancelled = true;
    };
  }, [state, ingest]);

  // 1Hz snapshot poll for order-book depth / KPIs / data-source banner. Also
  // drives staleness: >5s without a successful snapshot flips the warning on.
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const s = await fetchSnapshot(10);
        if (cancelled) return;
        lastSnapshotOkRef.current = Date.now();
        setStale(false);
        setSnapshot(s);
      } catch {
        if (!cancelled && Date.now() - lastSnapshotOkRef.current > 5000) setStale(true);
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

  return (
    <Ctx.Provider value={{ state, recent, snapshot, stale, restartedAt, nameOf }}>
      {children}
    </Ctx.Provider>
  );
}

export function useMarket(): MarketStreamValue {
  return useContext(Ctx);
}
