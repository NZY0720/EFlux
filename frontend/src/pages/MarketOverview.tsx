import AgentThoughtsFeed from "../components/AgentThoughtsFeed";
import DataSourceBanner from "../components/DataSourceBanner";
import { BoltIcon, LlmIcon, MarketIcon, ScaleIcon, type IconProps } from "../components/icons";
import IntroStrip from "../components/IntroStrip";
import KpiBar from "../components/KpiBar";
import MeritOrderChart from "../components/MeritOrderChart";
import OrderBookDepth from "../components/OrderBookDepth";
import PriceChart from "../components/PriceChart";
import TradeTape from "../components/TradeTape";
import { useMarket } from "../state/marketStream";

function CardTitle({ icon: Icon, children }: { icon: (p: IconProps) => React.ReactElement; children: React.ReactNode }) {
  return (
    <h3 className="mb-3 flex items-center gap-2 text-sm uppercase tracking-wide text-slate-400">
      <Icon size={15} className="text-sky-400/80" />
      {children}
    </h3>
  );
}

export default function MarketOverview() {
  // Stream + snapshot live in MarketStreamProvider (App level), so navigating
  // away and back keeps the accumulated chart/tape history.
  const { recent, snapshot } = useMarket();

  return (
    <div className="p-6 space-y-6">
      <IntroStrip />
      <KpiBar snapshot={snapshot} builtinVpps={snapshot?.num_builtin_vpps ?? 0} />
      <DataSourceBanner dataSource={snapshot?.data_source} />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <section className="eflux-card rounded-lg border border-slate-800 bg-slate-900/40 p-4 lg:col-span-2">
          <CardTitle icon={MarketIcon}>Merit order — who supplies at what price</CardTitle>
          <MeritOrderChart />
        </section>
        <section className="eflux-card rounded-lg border border-slate-800 bg-slate-900/40 p-4">
          <CardTitle icon={LlmIcon}>Agent thoughts (LLM)</CardTitle>
          <AgentThoughtsFeed />
        </section>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <section className="eflux-card rounded-lg border border-slate-800 bg-slate-900/40 p-4">
          <CardTitle icon={BoltIcon}>Price</CardTitle>
          <PriceChart events={recent} initialPrice={snapshot?.last_price ? Number(snapshot.last_price) : null} />
        </section>
        <section className="eflux-card rounded-lg border border-slate-800 bg-slate-900/40 p-4">
          <CardTitle icon={ScaleIcon}>Order book depth</CardTitle>
          <OrderBookDepth snapshot={snapshot} />
        </section>
      </div>

      <section className="eflux-card rounded-lg border border-slate-800 bg-slate-900/40 p-4">
        <CardTitle icon={MarketIcon}>Recent trades</CardTitle>
        <TradeTape events={recent} />
      </section>
    </div>
  );
}
