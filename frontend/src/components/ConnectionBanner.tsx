import { useState } from "react";

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
        <div className="flex items-center gap-2 border-b border-amber-900 bg-amber-950/60 px-6 py-2 text-sm text-amber-200">
          <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-amber-400" />
          Reconnecting to the market — data on screen may be stale.
        </div>
      )}
      {showRestart && (
        <div className="flex items-center justify-between gap-2 border-b border-sky-900 bg-sky-950/60 px-6 py-2 text-sm text-sky-200">
          <span>
            Backend restarted — the in-memory market started over, so price history and open
            orders were reset.
          </span>
          <button
            onClick={() => setDismissedAt(restartedAt)}
            className="shrink-0 text-sky-400 hover:text-sky-200"
          >
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}
