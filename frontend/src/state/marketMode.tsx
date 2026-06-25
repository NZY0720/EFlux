import { createContext, useContext, useEffect, useState } from "react";

import { fetchMeta, type MarketMode } from "../api/client";

interface MarketModeValue {
  /** Which market the backend is running. Defaults to "p2p" until the meta resolves. */
  mode: MarketMode;
  /** True once the backend meta has been fetched (success or failure). */
  ready: boolean;
}

const Ctx = createContext<MarketModeValue>({ mode: "p2p", ready: false });

/**
 * Fetches the backend market mode once on init. One market runs per launch
 * (chosen by which .command was started), so this never changes mid-session —
 * the dashboards and NavBar badge read it to render the right "story".
 */
export function MarketModeProvider({ children }: { children: React.ReactNode }) {
  const [mode, setMode] = useState<MarketMode>("p2p");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchMeta()
      .then((m) => {
        if (!cancelled && (m.market_mode === "p2p" || m.market_mode === "realprice")) {
          setMode(m.market_mode);
        }
      })
      .catch(() => {
        /* backend not ready — keep the p2p default */
      })
      .finally(() => {
        if (!cancelled) setReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return <Ctx.Provider value={{ mode, ready }}>{children}</Ctx.Provider>;
}

export function useMarketMode(): MarketModeValue {
  return useContext(Ctx);
}
