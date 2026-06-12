import { useState } from "react";
import { Link } from "react-router-dom";

const DISMISS_KEY = "eflux.intro.dismissed";

/** First-visit explainer: what the viewer is looking at, in three sentences. */
export default function IntroStrip() {
  const [dismissed, setDismissed] = useState(() => localStorage.getItem(DISMISS_KEY) === "1");
  if (dismissed) return null;

  return (
    <section className="rounded-lg border border-sky-900 bg-sky-950/30 px-4 py-3">
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1 text-sm text-slate-300">
          <p>
            <span className="font-semibold text-white">Welcome to EFlux.</span>{" "}
            <Link to="/participants" className="text-sky-400 hover:text-sky-300">
              30 autonomous virtual power plants
            </Link>{" "}
            — solar homes, wind farms, factories, gas peakers — trade electricity here in a live
            double auction.
          </p>
          <p>
            Supply stacks in <span className="text-white">merit order</span> (chart below): cheap
            solar and wind set the floor, batteries arbitrage the ~50 band, gas tops out at 55–72.
          </p>
          <p>
            One agent, <span className="text-emerald-300">my-llm-vpp</span>, is steered by an LLM —
            watch its live thoughts in the <span className="text-white">Agent thoughts</span> panel.
          </p>
        </div>
        <button
          onClick={() => {
            localStorage.setItem(DISMISS_KEY, "1");
            setDismissed(true);
          }}
          className="shrink-0 rounded border border-slate-700 px-2 py-1 text-xs text-slate-400 hover:text-white"
        >
          Got it
        </button>
      </div>
    </section>
  );
}
