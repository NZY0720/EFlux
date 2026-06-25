import { useEffect, useState } from "react";

import { fetchPpoStatus, renewPpos, type PpoStatus } from "../api/client";
import { useAuth } from "../state/auth";

const isRunning = (s?: string) => s === "training" || s === "reloading";

/**
 * Retrains every PPO (standalone, mirrors, and the hybrid agents' online executors)
 * on the latest ~1 month of real CAISO price + weather, then hot-reloads them into the
 * running market. Training happens in the background; this polls the status. Auth-gated
 * (like the speed control) — disabled until logged in.
 */
export default function RenewPpoButton() {
  const { token } = useAuth();
  const [status, setStatus] = useState<PpoStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const s = await fetchPpoStatus();
        if (!cancelled) setStatus(s);
      } catch {
        /* backend not ready — ignore */
      }
    };
    tick();
    const id = window.setInterval(tick, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  const running = isRunning(status?.state);

  const onClick = async () => {
    setError(null);
    try {
      setStatus(await renewPpos(30));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const finishedAt = status?.finished_at ? new Date(status.finished_at).toLocaleTimeString("en-GB", { hour12: false }) : null;
  const sub = error
    ? error
    : status?.state === "error"
      ? `failed: ${status.error ?? "unknown"}`
      : status?.state === "done"
        ? `${status.detail}${finishedAt ? ` · ${finishedAt}` : ""}`
        : running
          ? status?.detail || status?.state
          : "retrain on latest 1-month real data";

  return (
    <div className="flex flex-col items-end gap-1">
      <button
        onClick={onClick}
        disabled={running || !token}
        title={
          token
            ? "Retrain all PPOs on the latest 1-month real CAISO price + weather, then hot-reload them"
            : "Log in to renew PPOs"
        }
        className="flex items-center gap-1.5 rounded border border-slate-700 bg-slate-800 px-2.5 py-1 text-xs text-slate-200 transition-colors hover:bg-slate-700 disabled:opacity-50"
      >
        {running && <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-amber-400" />}
        {running ? `Renewing… (${status?.state})` : "↻ Renew PPOs"}
      </button>
      {sub && (
        <span className={`text-[11px] ${error || status?.state === "error" ? "text-rose-400" : "text-slate-500"}`}>
          {sub}
        </span>
      )}
    </div>
  );
}
