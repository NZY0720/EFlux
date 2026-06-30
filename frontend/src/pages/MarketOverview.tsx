import { useMarketMode } from "../state/marketMode";
import P2PMarketOverview from "./P2PMarketOverview";
import RealTimeMarketOverview from "./RealTimeMarketOverview";

/**
 * Market dashboard router. One market runs per launch, so this just picks the
 * dashboard that tells that market's story (selected by the backend's
 * market_mode, not a manual toggle).
 */
export default function MarketOverview() {
  const { mode, ready } = useMarketMode();
  // Wait for the backend meta so we don't flash the wrong dashboard's layout
  // (the two compositions differ) before the mode resolves.
  if (!ready) {
    return <div className="p-6 text-sm text-[var(--text-subtle)]">Loading market...</div>;
  }
  return mode === "realprice" ? <RealTimeMarketOverview /> : <P2PMarketOverview />;
}
