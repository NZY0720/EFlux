import { useState } from "react";
import { AlertTriangle, RotateCcw, X } from "lucide-react";

import { useMarket } from "../state/marketStream";

/**
 * App-wide health strip: amber while the backend is unreachable (WS down or
 * snapshots stale) so charts don't just silently freeze, plus a dismissible
 * notice when a backend restart is detected (the in-memory market starts over).
 */
export default function ConnectionBanner() {
  const { state, stale, restartedAt } = useMarket();
  const [dismissedAt, setDismissedAt] = useState<number | null>(null);

  const degraded = state !== "open" || stale;
  const showRestart = restartedAt !== null && restartedAt !== dismissedAt;
  if (!degraded && !showRestart) return null;

  return (
    <div className="space-y-px">
      {degraded && (
        <div className="flex items-center gap-2 border-b border-[color-mix(in_srgb,var(--warning)_42%,transparent)] bg-[var(--warning-soft)] px-4 py-2 text-sm text-[var(--warning)] md:px-6">
          <AlertTriangle size={16} />
          Reconnecting to the market - data on screen may be stale.
        </div>
      )}
      {showRestart && (
        <div className="flex items-center justify-between gap-2 border-b border-[color-mix(in_srgb,var(--accent)_42%,transparent)] bg-[var(--accent-soft)] px-4 py-2 text-sm text-[var(--accent)] md:px-6">
          <span className="flex items-center gap-2">
            <RotateCcw size={16} />
            Backend restarted - the in-memory market started over, so price history and open orders were reset.
          </span>
          <button
            onClick={() => setDismissedAt(restartedAt)}
            className="eflux-btn h-7 w-7 shrink-0 p-0"
            title="Dismiss"
            aria-label="Dismiss"
          >
            <X size={14} />
          </button>
        </div>
      )}
    </div>
  );
}
