import { useEffect, useRef, useState } from "react";

import type { MarketEvent } from "../api/types";

const WS_URL = (() => {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws/market`;
})();

export type ConnectionState = "connecting" | "open" | "closed";

interface Options {
  onEvent?: (e: MarketEvent) => void;
  maxBuffer?: number;
}

/**
 * Connects to /ws/market with auto-reconnect (capped exponential backoff).
 * Buffers the last N events in state for components that want to render a tail.
 */
export function useMarketStream(opts: Options = {}) {
  const { onEvent, maxBuffer = 200 } = opts;
  const [state, setState] = useState<ConnectionState>("connecting");
  const [recent, setRecent] = useState<MarketEvent[]>([]);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectMsRef = useRef(500);
  const stoppedRef = useRef(false);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    stoppedRef.current = false;
    let timeout: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      setState("connecting");
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        setState("open");
        reconnectMsRef.current = 500;
      };

      ws.onmessage = (msg) => {
        try {
          const e = JSON.parse(msg.data) as MarketEvent;
          onEventRef.current?.(e);
          setRecent((prev) => {
            const next = [e, ...prev];
            return next.length > maxBuffer ? next.slice(0, maxBuffer) : next;
          });
        } catch {
          // ignore malformed payload
        }
      };

      ws.onclose = () => {
        setState("closed");
        if (stoppedRef.current) return;
        const wait = Math.min(reconnectMsRef.current, 8000);
        timeout = setTimeout(connect, wait);
        reconnectMsRef.current = Math.min(reconnectMsRef.current * 2, 8000);
      };

      ws.onerror = () => {
        ws.close();
      };
    };

    connect();
    return () => {
      stoppedRef.current = true;
      if (timeout) clearTimeout(timeout);
      wsRef.current?.close();
    };
  }, [maxBuffer]);

  return { state, recent };
}
