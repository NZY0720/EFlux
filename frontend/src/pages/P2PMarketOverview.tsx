import { ChartCandlestick, ListChecks, MessagesSquare, Scale, Zap } from "lucide-react";

import Chatroom from "../components/Chatroom";
import { CardTitle, DashboardCard } from "../components/DashboardCard";
import DataSourceBanner from "../components/DataSourceBanner";
import IntroStrip from "../components/IntroStrip";
import KpiBar from "../components/KpiBar";
import MeritOrderChart from "../components/MeritOrderChart";
import OrderBookDepth from "../components/OrderBookDepth";
import PriceChart from "../components/PriceChart";
import RenewPpoButton from "../components/RenewPpoButton";
import TradeTape from "../components/TradeTape";
import { useMarket } from "../state/marketStream";

/**
 * P2P market dashboard: peer-to-peer continuous double auction. The story is
 * local price discovery and liquidity — who supplies at what price, the live
 * order book, and the emergent P2P price. CAISO is drawn only as a reference.
 */
export default function P2PMarketOverview() {
  // Stream + snapshot live in MarketStreamProvider (App level), so navigating
  // away and back keeps the accumulated chart/tape history.
  const { recent, snapshot } = useMarket();

  return (
    <div className="mx-auto w-full max-w-[1800px] space-y-6 px-4 py-5 md:p-6">
      <IntroStrip variant="p2p" />
      <KpiBar snapshot={snapshot} builtinVpps={snapshot?.num_builtin_vpps ?? 0} />
      <DataSourceBanner dataSource={snapshot?.data_source} showExternalPrice={false} />
      <div className="flex justify-end">
        <RenewPpoButton />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <DashboardCard className="lg:col-span-2">
          <CardTitle icon={ChartCandlestick}>Merit order - who supplies at what price</CardTitle>
          <MeritOrderChart />
        </DashboardCard>
        <DashboardCard>
          <CardTitle icon={MessagesSquare}>Agent chatroom</CardTitle>
          <Chatroom />
        </DashboardCard>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <DashboardCard>
          <CardTitle icon={Zap}>Price - emergent local P2P price</CardTitle>
          <PriceChart
            variant="p2p"
            events={recent}
            initialPrice={snapshot?.last_price ? Number(snapshot.last_price) : null}
          />
        </DashboardCard>
        <DashboardCard>
          <CardTitle icon={Scale}>Order book depth</CardTitle>
          <OrderBookDepth snapshot={snapshot} />
        </DashboardCard>
      </div>

      <DashboardCard>
        <CardTitle icon={ListChecks}>Recent trades (peer-to-peer)</CardTitle>
        <TradeTape events={recent} />
      </DashboardCard>
    </div>
  );
}
