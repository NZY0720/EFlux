import { BarChart3, ChartNoAxesCombined, Scale, TrendingUp, Zap } from "lucide-react";

import { CardTitle, DashboardCard, StatusPill } from "../components/DashboardCard";
import DataSourceBanner from "../components/DataSourceBanner";
import EquityCurves from "../components/EquityCurves";
import GridSettlementPanel from "../components/GridSettlementPanel";
import IntroStrip from "../components/IntroStrip";
import KpiBar from "../components/KpiBar";
import LlmPpoInfluencePanel from "../components/LlmPpoInfluencePanel";
import MarketActivityRail from "../components/MarketActivityRail";
import PriceChart from "../components/PriceChart";
import RenewPpoButton from "../components/RenewPpoButton";
import StrategyLeaderboard from "../components/StrategyLeaderboard";
import { useMarket } from "../state/marketStream";
import { useStrategyPnl } from "../state/useStrategyPnl";

/** Real-time price market: side-specific grid settlement and strategy response lead. */
export default function RealTimeMarketOverview() {
  const { recent, snapshot } = useMarket();
  const { agents, history } = useStrategyPnl();
  const provenanceTone = snapshot?.data_provenance === "real" ? "success" : snapshot?.data_provenance === "cached" ? "amber" : "muted";
  const external = snapshot?.external_market;
  const initialExternalPrice = external && (external.status === "real" || external.status === "fallback") ? Number(external.raw_lmp) : null;
  const initialImportPrice = external && (external.status === "real" || external.status === "fallback") ? Number(external.import_price) : null;
  const initialExportPrice = external && (external.status === "real" || external.status === "fallback") ? Number(external.export_price) : null;

  return (
    <div className="mx-auto w-full max-w-[1800px] space-y-4 px-4 py-5 md:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-[var(--text)]">Real-time price market</h1>
          <p className="mt-1 text-sm text-[var(--text-muted)]">Grid price, settlement band, and strategy response.</p>
        </div>
        <div className="flex items-center gap-2">
          <StatusPill tone="amber">Real-time price</StatusPill>
          {snapshot && <StatusPill tone={provenanceTone}>data: {snapshot.data_provenance}</StatusPill>}
        </div>
      </div>

      <KpiBar compact variant="realprice" snapshot={snapshot} builtinVpps={snapshot?.num_builtin_vpps ?? 0} />

      <DashboardCard>
        <CardTitle icon={Zap} accent="amber">CAISO settlement price trend</CardTitle>
        <PriceChart
          variant="realprice"
          events={recent}
          initialExternalPrice={initialExternalPrice}
          initialImportPrice={initialImportPrice}
          initialExportPrice={initialExportPrice}
        />
      </DashboardCard>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <DashboardCard className="min-w-0 lg:col-span-2">
          <CardTitle icon={Scale} accent="amber">Grid settlement and local position</CardTitle>
          <GridSettlementPanel snapshot={snapshot} />
        </DashboardCard>
        <aside className="min-w-0" aria-label="Real-time market activity">
          <MarketActivityRail snapshot={snapshot} events={recent} variant="realprice" />
        </aside>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_auto] xl:items-start">
        <DataSourceBanner dataSource={snapshot?.data_source} />
        <div className="xl:pt-3"><RenewPpoButton /></div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
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
      <IntroStrip variant="realprice" />
    </div>
  );
}
