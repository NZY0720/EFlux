import DataSourceBanner from "../components/DataSourceBanner";
import KpiBar from "../components/KpiBar";
import OrderBookDepth from "../components/OrderBookDepth";
import PriceChart from "../components/PriceChart";
import TradeTape from "../components/TradeTape";
import { useMarket } from "../state/marketStream";

export default function MarketOverview() {
  // Stream + snapshot live in MarketStreamProvider (App level), so navigating
  // away and back keeps the accumulated chart/tape history.
  const { recent, snapshot } = useMarket();

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
