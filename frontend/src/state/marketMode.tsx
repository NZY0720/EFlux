import { createContext, useContext, useEffect, useState } from "react";

import { fetchMeta, type MarketMode } from "../api/client";

interface MarketModeValue {
  /** Which market the backend is running. Defaults to "p2p" until the meta resolves. */
  mode: MarketMode;
  /** True once the backend meta has been fetched (success or failure). */
  ready: boolean;
}

const Ctx = createContext<MarketModeValue>({ mode: "p2p", ready: false });

const RETRY_DELAYS = [2000, 5000, 10_000, 30_000];

export function MarketModeProvider({ children }: { children: React.ReactNode }) {
  const [mode, setMode] = useState<MarketMode>("p2p");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let retry = 0;
    let timer: number | undefined;
    const load = async () => {
      let nextMode: MarketMode | null = null;
      try {
        const meta = await fetchMeta();
        if (meta.market_mode === "p2p" || meta.market_mode === "realprice") {
          nextMode = meta.market_mode;
        }
      } catch {
        /* backend may still be starting */
      }
      if (cancelled) return;
      setReady(true);
      if (nextMode) {
        setMode(nextMode);
        return;
      }
      const delay = RETRY_DELAYS[Math.min(retry, RETRY_DELAYS.length - 1)];
      retry += 1;
      timer = window.setTimeout(load, delay);
    };
    void load();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, []);

  return <Ctx.Provider value={{ mode, ready }}>{children}</Ctx.Provider>;
}

export function useMarketMode(): MarketModeValue {
  return useContext(Ctx);
}
