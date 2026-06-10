import { useEffect, useRef, useState } from "react";

import type { MarketEvent } from "../api/types";

const WS_URL = (() => {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws/market`;
})();

export type ConnectionState = "connecting" | "open" | "closed";

interface Options {
  onEvent?: (e: MarketEvent) => void;
}

/**
 * Connects to /ws/market with auto-reconnect (capped exponential backoff).
 * Delivers events via onEvent; buffering/dedup lives in MarketStreamProvider.
 *
 * The lifecycle flag and reconnect timer are local to each effect run: a
 * previous version shared a `stoppedRef` across runs, so StrictMode's
 * mount→unmount→mount cycle reset the flag and the first (dead) effect's
 * onclose scheduled a zombie reconnect — leaving two live sockets that
 * delivered every event twice.
 */
export function useMarketStream(opts: Options = {}) {
  const { onEvent } = opts;
  const [state, setState] = useState<ConnectionState>("connecting");
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    let stopped = false;
    let ws: WebSocket | null = null;
    let timeout: ReturnType<typeof setTimeout> | null = null;
    let reconnectMs = 500;

    const connect = () => {
      if (stopped) return;
      setState("connecting");
      ws = new WebSocket(WS_URL);

      ws.onopen = () => {
        setState("open");
        reconnectMs = 500;
      };

      ws.onmessage = (msg) => {
        try {
          const e = JSON.parse(msg.data) as MarketEvent;
          onEventRef.current?.(e);
        } catch {
          // ignore malformed payload
        }
      };

      ws.onclose = () => {
        setState("closed");
        if (stopped) return;
        timeout = setTimeout(connect, reconnectMs);
        reconnectMs = Math.min(reconnectMs * 2, 8000);
      };

      ws.onerror = () => {
        ws?.close();
      };
    };

    connect();
    return () => {
      stopped = true;
      if (timeout) clearTimeout(timeout);
      ws?.close();
    };
  }, []);

  return { state };
}
