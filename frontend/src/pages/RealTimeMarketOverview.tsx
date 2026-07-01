import { BarChart3, ChartNoAxesCombined, ListChecks, MessagesSquare, TrendingUp, Zap } from "lucide-react";

import Chatroom from "../components/Chatroom";
import { CardTitle, DashboardCard } from "../components/DashboardCard";
import DataSourceBanner from "../components/DataSourceBanner";
import EquityCurves from "../components/EquityCurves";
import IntroStrip from "../components/IntroStrip";
import KpiBar from "../components/KpiBar";
import LlmPpoInfluencePanel from "../components/LlmPpoInfluencePanel";
import PriceChart from "../components/PriceChart";
import RenewPpoButton from "../components/RenewPpoButton";
import StrategyLeaderboard from "../components/StrategyLeaderboard";
import TradeTape from "../components/TradeTape";
import { useMarket } from "../state/marketStream";
import { useStrategyPnl } from "../state/useStrategyPnl";

/**
 * Real-Time price market dashboard: agents are pure price-takers against the
 * live CAISO price. There is no peer order book — the story is strategy
 * performance, so the leaderboard and equity curves take center stage.
 */
export default function RealTimeMarketOverview() {
  const { recent, snapshot } = useMarket();
  const { agents, history } = useStrategyPnl();

  return (
    <div className="mx-auto w-full max-w-[1800px] space-y-6 px-4 py-5 md:p-6">
      <IntroStrip variant="realprice" />
      <KpiBar variant="realprice" snapshot={snapshot} builtinVpps={snapshot?.num_builtin_vpps ?? 0} />
      <DataSourceBanner dataSource={snapshot?.data_source} />
      <div className="flex justify-end">
        <RenewPpoButton />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <DashboardCard className="lg:col-span-2">
          <CardTitle icon={Zap} accent="amber">CAISO price - the market you trade against</CardTitle>
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
        </DashboardCard>
        <DashboardCard>
          <CardTitle icon={MessagesSquare} accent="amber">Agent chatroom</CardTitle>
          <Chatroom />
        </DashboardCard>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <DashboardCard>
          <CardTitle icon={TrendingUp} accent="amber">Strategy leaderboard - PnL vs the grid</CardTitle>
          <StrategyLeaderboard agents={agents} />
        </DashboardCard>
        <DashboardCard>
          <CardTitle icon={ChartNoAxesCombined} accent="amber">Equity curves</CardTitle>
          <EquityCurves history={history} />
        </DashboardCard>
      </div>

      <DashboardCard>
        <CardTitle icon={BarChart3} accent="amber">LLM to PPO influence - hybrid vs mirror</CardTitle>
        <LlmPpoInfluencePanel agents={agents} />
      </DashboardCard>

      <DashboardCard>
        <CardTitle icon={ListChecks} accent="amber">Grid trades (vs CAISO)</CardTitle>
        <TradeTape events={recent} />
      </DashboardCard>
    </div>
  );
}
