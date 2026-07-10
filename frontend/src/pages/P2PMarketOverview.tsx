import { ChartCandlestick, Zap } from "lucide-react";

import { CardTitle, DashboardCard, StatusPill } from "../components/DashboardCard";
import DataSourceBanner from "../components/DataSourceBanner";
import IntroStrip from "../components/IntroStrip";
import KpiBar from "../components/KpiBar";
import MarketActivityRail from "../components/MarketActivityRail";
import MeritOrderChart from "../components/MeritOrderChart";
import PriceChart from "../components/PriceChart";
import RenewPpoButton from "../components/RenewPpoButton";
import { useMarket } from "../state/marketStream";

/** P2P market: discovery and order flow lead; supporting details follow. */
export default function P2PMarketOverview() {
  const { recent, snapshot } = useMarket();
  const provenanceTone = snapshot?.data_provenance === "real" ? "success" : snapshot?.data_provenance === "cached" ? "amber" : "muted";

  return (
    <div className="mx-auto w-full max-w-[1800px] space-y-4 px-4 py-5 md:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-[var(--text)]">Live P2P market</h1>
          <p className="mt-1 text-sm text-[var(--text-muted)]">Local price discovery and liquidity, in real time.</p>
        </div>
        <div className="flex items-center gap-2">
          <StatusPill tone="accent">P2P market</StatusPill>
          {snapshot && <StatusPill tone={provenanceTone}>data: {snapshot.data_provenance}</StatusPill>}
        </div>
      </div>

      <KpiBar compact snapshot={snapshot} builtinVpps={snapshot?.num_builtin_vpps ?? 0} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="min-w-0 space-y-4 lg:col-span-2">
          <DashboardCard>
            <CardTitle icon={ChartCandlestick}>Merit order - supply and demand</CardTitle>
            <MeritOrderChart />
          </DashboardCard>
          <DashboardCard>
            <CardTitle icon={Zap}>Price trend</CardTitle>
            <PriceChart
              variant="p2p"
              events={recent}
              initialPrice={snapshot?.last_price !== null && snapshot?.last_price !== undefined ? Number(snapshot.last_price) : null}
            />
          </DashboardCard>
        </div>
        <aside className="min-w-0" aria-label="P2P market activity">
          <MarketActivityRail snapshot={snapshot} events={recent} variant="p2p" />
        </aside>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_auto] xl:items-start">
        <DataSourceBanner dataSource={snapshot?.data_source} showExternalPrice={false} />
        <div className="xl:pt-3"><RenewPpoButton /></div>
      </div>
      <IntroStrip variant="p2p" />
    </div>
  );
}
