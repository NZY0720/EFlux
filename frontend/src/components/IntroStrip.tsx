import { useState } from "react";
import { Link } from "react-router-dom";
import { Info, X } from "lucide-react";

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
          <span className="font-semibold text-[var(--text)]">Real-time price market.</span>{" "}
          <Link to="/participants" className="text-[var(--warning)] hover:underline">
            Strategy agents
          </Link>{" "}
          are pure price-takers against the live <span className="text-[var(--text)]">CAISO</span> price: every
          order settles against the grid at import/export, and their volume never moves the price.
        </p>
        <p>
          There is <span className="text-[var(--text)]">no peer order book</span> here - the point is to test
          how each strategy times the market. Watch the <span className="text-[var(--text)]">leaderboard</span>{" "}
          and <span className="text-[var(--text)]">equity curves</span> to see which earns the most.
        </p>
        <p>
          PPO and LLM-coached agents trade alongside <span className="text-[var(--success)]">truthful</span>{" "}
          cost-based baselines for comparison.
        </p>
      </>
    ) : (
      <>
        <p>
          <span className="font-semibold text-[var(--text)]">Welcome to EFlux - P2P market.</span>{" "}
          <Link to="/participants" className="text-[var(--accent)] hover:underline">
            Autonomous virtual power plants
          </Link>{" "}
          - solar homes, wind farms, factories, gas peakers - trade electricity with{" "}
          <span className="text-[var(--text)]">each other</span> in a live double auction.
        </p>
        <p>
          Prices emerge from supply and demand: agents set their own bids and asks chasing profit, so
          the cleared price reflects this local market on its own terms.
        </p>
        <p>
          One agent, <span className="text-[var(--success)]">my-llm-vpp</span>, is steered by an LLM - watch
          its live thoughts in the <span className="text-[var(--text)]">Agent thoughts</span> panel.
        </p>
      </>
    );

  const borderCls =
    variant === "realprice"
      ? "border-[color-mix(in_srgb,var(--warning)_42%,transparent)] bg-[var(--warning-soft)]"
      : "border-[color-mix(in_srgb,var(--accent)_42%,transparent)] bg-[var(--accent-soft)]";
  const iconCls = variant === "realprice" ? "text-[var(--warning)]" : "text-[var(--accent)]";

  return (
    <section className={`rounded-lg border px-4 py-3 ${borderCls}`}>
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 gap-3">
          <Info size={18} className={`mt-0.5 shrink-0 ${iconCls}`} />
          <div className="min-w-0 space-y-1 break-words text-sm leading-6 text-[var(--text-muted)]">{body}</div>
        </div>
        <button
          onClick={() => {
            localStorage.setItem(dismissKey, "1");
            setDismissed(true);
          }}
          className="eflux-btn h-8 w-8 shrink-0 p-0"
          title="Dismiss"
          aria-label="Dismiss"
        >
          <X size={15} />
        </button>
      </div>
    </section>
  );
}
