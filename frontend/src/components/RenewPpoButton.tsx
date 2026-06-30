import { useEffect, useState } from "react";
import { LoaderCircle, RefreshCcw } from "lucide-react";

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
        ? `${status.detail}${finishedAt ? ` / ${finishedAt}` : ""}`
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
        className="eflux-btn h-8 px-3 text-xs disabled:opacity-50"
      >
        {running ? <LoaderCircle size={14} className="animate-spin text-[var(--warning)]" /> : <RefreshCcw size={14} />}
        {running ? `Renewing... (${status?.state})` : "Renew PPOs"}
      </button>
      {sub && (
        <span className={`text-[11px] ${error || status?.state === "error" ? "text-[var(--danger)]" : "text-[var(--text-subtle)]"}`}>
          {sub}
        </span>
      )}
    </div>
  );
}
