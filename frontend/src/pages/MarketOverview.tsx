import { useEffect, useState } from "react";

import DataSourceBanner from "../components/DataSourceBanner";
import KpiBar from "../components/KpiBar";
import OrderBookDepth from "../components/OrderBookDepth";
import PriceChart from "../components/PriceChart";
import TradeTape from "../components/TradeTape";
import { fetchSnapshot } from "../api/client";
import type { MarketSnapshot } from "../api/types";
import { useMarketStream } from "../ws/useMarketStream";

export default function MarketOverview() {
  const { recent, state } = useMarketStream({ maxBuffer: 500 });
  const [snapshot, setSnapshot] = useState<MarketSnapshot | null>(null);

  // Pull a snapshot periodically (REST) for order book depth — WS pushes per-tick summary
  // but not full depth. Cheap and good enough at 1Hz.
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const s = await fetchSnapshot(10);
        if (!cancelled) setSnapshot(s);
      } catch {
        /* swallow — connection issues handled by indicator */
      }
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  // Re-export state for NavBar via window event (small cross-cut to avoid prop drilling).
  useEffect(() => {
    window.dispatchEvent(new CustomEvent("eflux:ws-state", { detail: state }));
  }, [state]);

  return (
    <div className="p-6 space-y-6">
      <KpiBar snapshot={snapshot} builtinVpps={snapshot?.num_builtin_vpps ?? 0} />
      <DataSourceBanner dataSource={snapshot?.data_source} />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <section className="rounded-lg border border-slate-800 bg-slate-900/40 p-4">
          <h3 className="text-sm uppercase tracking-wide text-slate-400 mb-3">Price</h3>
          <PriceChart events={recent} initialPrice={snapshot?.last_price ? Number(snapshot.last_price) : null} />
        </section>
        <section className="rounded-lg border border-slate-800 bg-slate-900/40 p-4">
          <h3 className="text-sm uppercase tracking-wide text-slate-400 mb-3">Order book depth</h3>
          <OrderBookDepth snapshot={snapshot} />
        </section>
      </div>

      <section className="rounded-lg border border-slate-800 bg-slate-900/40 p-4">
        <h3 className="text-sm uppercase tracking-wide text-slate-400 mb-3">Recent trades</h3>
        <TradeTape events={recent} />
      </section>
    </div>
  );
}
