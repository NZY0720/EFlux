import AgentThoughtsFeed from "../components/AgentThoughtsFeed";
import DataSourceBanner from "../components/DataSourceBanner";
import EquityCurves from "../components/EquityCurves";
import { BoltIcon, LlmIcon, MarketIcon, ScaleIcon, TrendUpIcon, type IconProps } from "../components/icons";
import IntroStrip from "../components/IntroStrip";
import KpiBar from "../components/KpiBar";
import LlmPpoInfluencePanel from "../components/LlmPpoInfluencePanel";
import PriceChart from "../components/PriceChart";
import RenewPpoButton from "../components/RenewPpoButton";
import StrategyLeaderboard from "../components/StrategyLeaderboard";
import TradeTape from "../components/TradeTape";
import { useMarket } from "../state/marketStream";
import { useStrategyPnl } from "../state/useStrategyPnl";

function CardTitle({ icon: Icon, children }: { icon: (p: IconProps) => React.ReactElement; children: React.ReactNode }) {
  return (
    <h3 className="mb-3 flex items-center gap-2 text-sm uppercase tracking-wide text-slate-400">
      <Icon size={15} className="text-amber-400/80" />
      {children}
    </h3>
  );
}

/**
 * Real-Time price market dashboard: agents are pure price-takers against the
 * live CAISO price. There is no peer order book — the story is strategy
 * performance, so the leaderboard and equity curves take center stage.
 */
export default function RealTimeMarketOverview() {
  const { recent, snapshot } = useMarket();
  const { agents, history } = useStrategyPnl();

  return (
    <div className="p-6 space-y-6">
      <IntroStrip variant="realprice" />
      <KpiBar variant="realprice" snapshot={snapshot} builtinVpps={snapshot?.num_builtin_vpps ?? 0} />
      <DataSourceBanner dataSource={snapshot?.data_source} />
      <div className="flex justify-end">
        <RenewPpoButton />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <section className="eflux-card rounded-lg border border-slate-800 bg-slate-900/40 p-4 lg:col-span-2">
          <CardTitle icon={BoltIcon}>CAISO price — the market you trade against</CardTitle>
          <PriceChart
            variant="realprice"
            events={recent}
            initialExternalPrice={
              snapshot?.external_market &&
              (snapshot.external_market.status === "real" || snapshot.external_market.status === "fallback") &&
              snapshot.external_market.raw_lmp
                ? Number(snapshot.external_market.raw_lmp)
                : null
            }
          />
        </section>
        <section className="eflux-card rounded-lg border border-slate-800 bg-slate-900/40 p-4">
          <CardTitle icon={LlmIcon}>Agent thoughts (LLM)</CardTitle>
          <AgentThoughtsFeed variant="realprice" />
        </section>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <section className="eflux-card rounded-lg border border-slate-800 bg-slate-900/40 p-4">
          <CardTitle icon={TrendUpIcon}>Strategy leaderboard — PnL vs the grid</CardTitle>
          <StrategyLeaderboard agents={agents} />
        </section>
        <section className="eflux-card rounded-lg border border-slate-800 bg-slate-900/40 p-4">
          <CardTitle icon={ScaleIcon}>Equity curves</CardTitle>
          <EquityCurves history={history} />
        </section>
      </div>

      <section className="eflux-card rounded-lg border border-slate-800 bg-slate-900/40 p-4">
        <CardTitle icon={LlmIcon}>LLM to PPO influence — hybrid vs mirror</CardTitle>
        <LlmPpoInfluencePanel agents={agents} />
      </section>

      <section className="eflux-card rounded-lg border border-slate-800 bg-slate-900/40 p-4">
        <CardTitle icon={MarketIcon}>Grid trades (vs CAISO)</CardTitle>
        <TradeTape events={recent} />
      </section>
    </div>
  );
}
