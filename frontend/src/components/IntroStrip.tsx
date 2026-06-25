import { useState } from "react";
import { Link } from "react-router-dom";

interface Props {
  variant?: "p2p" | "realprice";
}

/** First-visit explainer: what the viewer is looking at, in three sentences. */
export default function IntroStrip({ variant = "p2p" }: Props) {
  const dismissKey = `eflux.intro.dismissed.${variant}`;
  const [dismissed, setDismissed] = useState(() => localStorage.getItem(dismissKey) === "1");
  if (dismissed) return null;

  const body =
    variant === "realprice" ? (
      <>
        <p>
          <span className="font-semibold text-white">Real-time price market.</span>{" "}
          <Link to="/participants" className="text-amber-400 hover:text-amber-300">
            Strategy agents
          </Link>{" "}
          are pure price-takers against the live <span className="text-white">CAISO</span> price: every
          order settles against the grid at import/export, and their volume never moves the price.
        </p>
        <p>
          There is <span className="text-white">no peer order book</span> here — the point is to test
          how each strategy times the market. Watch the <span className="text-white">leaderboard</span>{" "}
          and <span className="text-white">equity curves</span> to see which earns the most.
        </p>
        <p>
          PPO and LLM-coached agents trade alongside <span className="text-emerald-300">truthful</span>{" "}
          cost-based baselines for comparison.
        </p>
      </>
    ) : (
      <>
        <p>
          <span className="font-semibold text-white">Welcome to EFlux — P2P market.</span>{" "}
          <Link to="/participants" className="text-sky-400 hover:text-sky-300">
            Autonomous virtual power plants
          </Link>{" "}
          — solar homes, wind farms, factories, gas peakers — trade electricity with{" "}
          <span className="text-white">each other</span> in a live double auction.
        </p>
        <p>
          Prices emerge from supply and demand: agents set their own bids and asks chasing profit, so
          the cleared price reflects this local market on its own terms.
        </p>
        <p>
          One agent, <span className="text-emerald-300">my-llm-vpp</span>, is steered by an LLM — watch
          its live thoughts in the <span className="text-white">Agent thoughts</span> panel.
        </p>
      </>
    );

  const borderCls =
    variant === "realprice" ? "border-amber-900 bg-amber-950/30" : "border-sky-900 bg-sky-950/30";

  return (
    <section className={`rounded-lg border px-4 py-3 ${borderCls}`}>
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1 text-sm text-slate-300">{body}</div>
        <button
          onClick={() => {
            localStorage.setItem(dismissKey, "1");
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
