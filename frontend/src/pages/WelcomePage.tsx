import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  Bot,
  CloudSun,
  Gauge,
  MessagesSquare,
  Swords,
  TerminalSquare,
  Trophy,
  Zap,
} from "lucide-react";

import { fetchChatter, fetchMarketAgents, fetchMarketReflections } from "../api/client";
import type { ChatMessage, MarketEvent } from "../api/types";
import BrandLogo from "../components/BrandLogo";
import GridCanvas, { type GridCanvasHandle } from "../components/welcome/GridCanvas";
import { useMarketMode } from "../state/marketMode";
import { useMarket } from "../state/marketStream";
import marketShot from "../../../docs/img/market.png";
import participantsShot from "../../../docs/img/participants.png";

/**
 * Welcome page: a cinematic, live-data introduction to EFlux.
 * Locked dark theme (wl-cinema) + liquid-glass panels (honest web approximation,
 * see index.css). The hero canvas is driven by the real market: every trade the
 * simulator clears fires a pulse across the grid.
 */

/* ------------------------------------------------------------------ */
/* Scroll reveal (IntersectionObserver adds .is-in; CSS does the rest) */
/* ------------------------------------------------------------------ */

function Reveal({
  children,
  className = "",
  delay = 0,
}: {
  children: React.ReactNode;
  className?: string;
  delay?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  // State (not an imperative class): the page re-renders every second with live
  // market data, and a prop-driven className would wipe an imperatively-added one.
  const [inView, setInView] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setInView(true);
          io.disconnect();
        }
      },
      { threshold: 0.15 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return (
    <div
      ref={ref}
      className={`wl-reveal ${inView ? "is-in" : ""} ${className}`}
      style={delay ? { transitionDelay: `${delay}ms` } : undefined}
    >
      {children}
    </div>
  );
}

/* --------------------------- live-data hooks --------------------------- */

const isTrade = (e: MarketEvent) => e.kind === "trade" || e.kind === "external.trade";
const tradeKey = (e: MarketEvent) =>
  e.kind === "trade" ? `t${e.trade_id}` : e.kind === "external.trade" ? `x${e.external_trade_id}` : "";

function useAgentVoices() {
  const [chatter, setChatter] = useState<ChatMessage[]>([]);
  const [rationale, setRationale] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const [chat, refl] = await Promise.all([
          fetchChatter(8),
          fetchMarketReflections(5),
        ]);
        if (cancelled) return;
        setChatter(chat.slice(-4));
        const withText = refl.find((r) => r.rationale);
        setRationale(withText?.rationale ?? null);
      } catch {
        /* backend offline: the panel shows its quiet state */
      }
    };
    tick();
    const id = setInterval(tick, 7000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);
  return { chatter, rationale };
}

/* ------------------------------- page ---------------------------------- */

export default function WelcomePage() {
  const { snapshot, recent, state: wsState, nameOf } = useMarket();
  const { mode } = useMarketMode();
  const canvasRef = useRef<GridCanvasHandle>(null);
  const lastTradeRef = useRef<string | null>(null);
  const [roster, setRoster] = useState<{ name: string; category: string }[]>();
  const { chatter, rationale } = useAgentVoices();

  useEffect(() => {
    fetchMarketAgents()
      .then((agents) => setRoster(agents.map((a) => ({ name: a.name, category: a.category }))))
      .catch(() => {});
  }, []);

  // Real trades drive the hero: diff the newest-first event buffer, fire pulses.
  const trades = recent.filter(isTrade);
  useEffect(() => {
    if (trades.length === 0) return;
    const keys = trades.map(tradeKey);
    const idx = lastTradeRef.current ? keys.indexOf(lastTradeRef.current) : -1;
    const fresh = idx === -1 ? Math.min(trades.length, 3) : idx;
    if (fresh > 0) canvasRef.current?.pulse(fresh);
    lastTradeRef.current = keys[0];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recent]);

  const lastPrice = snapshot?.last_price ? Number(snapshot.last_price) : null;
  const live = wsState === "open" && snapshot !== null;
  const modeLabel = mode === "realprice" ? "Real-time price" : "Peer-to-peer";
  const balance = snapshot?.balance;

  return (
    <div className="wl-cinema min-h-screen">
      {/* ------------------------------ HERO ------------------------------ */}
      <section className="relative flex min-h-[100dvh] flex-col overflow-hidden">
        <GridCanvas ref={canvasRef} roster={roster} className="absolute inset-0" />
        {/* Legibility scrim behind the copy, transparent toward the living grid. */}
        <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(100deg,rgba(5,9,16,0.86)_0%,rgba(5,9,16,0.55)_38%,transparent_66%)]" />

        <header className="relative z-10 mx-auto flex w-full max-w-[1400px] items-center justify-between px-4 py-5 md:px-8">
          <Link to="/" className="flex items-center gap-2.5">
            <BrandLogo size={34} />
            <span className="eflux-wordmark text-xl font-bold">EFlux</span>
          </Link>
          <Link to="/login" className="lg-btn h-10 px-5 text-sm font-medium">
            Sign in
          </Link>
        </header>

        <div className="relative z-10 mx-auto flex w-full max-w-[1400px] flex-1 items-center px-4 pb-24 md:px-8">
          <div className="max-w-2xl">
            <h1 className="wl-hero-rise text-5xl font-semibold tracking-tight text-[var(--text)] sm:text-6xl lg:text-7xl">
              The grid learns
              <br />
              to trade.
            </h1>
            <p
              className="wl-hero-rise mt-6 max-w-xl text-lg leading-relaxed text-[var(--text-muted)] md:text-xl"
              style={{ animationDelay: "120ms" }}
            >
              Autonomous agents run virtual power plants inside a live electricity
              market. Watch them think, compete, and settle.
            </p>
            <div className="wl-hero-rise mt-9 flex flex-wrap gap-3" style={{ animationDelay: "240ms" }}>
              <Link to="/market" className="lg-btn lg-btn-primary h-12 px-7 text-base">
                Enter the market
                <ArrowRight size={17} />
              </Link>
              <Link to="/vpps" className="lg-btn h-12 px-7 text-base">
                Deploy an agent
              </Link>
            </div>
          </div>
        </div>

        {/* Live ticker: real numbers from the running simulator. */}
        <div className="wl-hero-rise relative z-10 mx-4 mb-6 md:absolute md:bottom-8 md:right-8 md:mx-0 md:mb-0" style={{ animationDelay: "360ms" }}>
          <div className="lg-glass flex items-center gap-5 px-5 py-3.5 text-sm">
            <span className="flex items-center gap-2">
              <span
                className={`inline-block h-2 w-2 rounded-full ${live ? "bg-[var(--success)] eflux-live-dot" : "bg-[var(--warning)]"}`}
              />
              <span className="font-medium text-[var(--text)]">
                {live ? "Market live" : "Market offline"}
              </span>
            </span>
            <span className="hidden text-[var(--text-subtle)] sm:inline">{modeLabel}</span>
            {lastPrice !== null && (
              <span className="tabular-nums text-[var(--text)]">
                {lastPrice.toFixed(1)}
                <span className="ml-1 text-xs text-[var(--text-subtle)]">$/MWh</span>
              </span>
            )}
            {snapshot && (
              <span className="hidden tabular-nums text-[var(--text-muted)] md:inline">
                {snapshot.num_builtin_vpps} agents
              </span>
            )}
          </div>
        </div>
      </section>

      {/* ------------------------------ AGENT ----------------------------- */}
      <section className="mx-auto grid w-full max-w-[1400px] grid-cols-1 items-center gap-12 px-4 py-24 md:px-8 lg:grid-cols-[1.1fr_0.9fr] lg:py-36">
        <Reveal>
          <h2 className="text-4xl font-semibold tracking-tight text-[var(--text)] md:text-5xl">
            Agents that explain themselves.
          </h2>
          <p className="mt-6 max-w-xl text-lg leading-relaxed text-[var(--text-muted)]">
            Inside every virtual power plant, an LLM strategist sets intent, a PPO
            executor works the order book, and a single risk gate holds veto power.
          </p>
          <p className="mt-4 max-w-xl text-lg leading-relaxed text-[var(--text-muted)]">
            Nothing is a black box: strategy, rationale, and every fill stream into
            the open. This chatter is them, talking, now.
          </p>
        </Reveal>
        <Reveal delay={140}>
          <div className="lg-glass flex min-h-[300px] flex-col p-6">
            <div className="flex items-center gap-2 text-sm font-medium text-[var(--text)]">
              <MessagesSquare size={16} className="text-[var(--accent)]" />
              Agent chatter
              <span className="ml-auto text-xs font-normal text-[var(--text-subtle)]">live feed</span>
            </div>
            <div className="mt-4 flex flex-1 flex-col justify-center space-y-3 text-sm">
              {chatter.length === 0 ? (
                <p className="py-6 text-center text-[var(--text-subtle)]">
                  The room is quiet. When the market runs with an LLM configured,
                  the agents banter here.
                </p>
              ) : (
                chatter.map((m, i) => (
                  <div key={`${m.wall_ts}-${i}`} className="flex gap-3">
                    <span className="mt-0.5 shrink-0 text-xs font-semibold text-[var(--accent)]">
                      {m.name}
                    </span>
                    <span className="text-[var(--text-muted)]">{m.text}</span>
                  </div>
                ))
              )}
            </div>
            {rationale && (
              <div className="mt-5 border-t border-[var(--border)] pt-4">
                <div className="flex items-center gap-2 text-xs text-[var(--text-subtle)]">
                  <Bot size={13} />
                  Latest strategy note
                </div>
                <p className="mt-2 text-sm italic leading-relaxed text-[var(--text-muted)]">
                  "{rationale}"
                </p>
              </div>
            )}
          </div>
        </Reveal>
      </section>

      {/* ------------------------------- GRID ------------------------------ */}
      <section className="border-y border-[var(--border)] bg-[rgba(8,14,26,0.5)]">
        <div className="mx-auto w-full max-w-[1400px] px-4 py-24 md:px-8 lg:py-32">
          <Reveal>
            <h2 className="max-w-2xl text-4xl font-semibold tracking-tight text-[var(--text)] md:text-5xl">
              Real weather. Real prices. Real physics.
            </h2>
          </Reveal>
          <div className="mt-12 grid grid-cols-1 gap-4 md:grid-cols-[1.6fr_1fr_1fr]">
            <Reveal delay={80}>
              <div className="lg-glass h-full p-6">
                <div className="flex items-center gap-2 text-sm font-medium text-[var(--text)]">
                  <Gauge size={16} className="text-[var(--accent)]" />
                  System balance, right now
                </div>
                {balance ? (
                  <div className="mt-6 grid grid-cols-3 gap-4">
                    <BalanceStat label="Renewables" value={balance.renewable_kw} unit="kW" />
                    <BalanceStat label="Load" value={balance.load_kw} unit="kW" />
                    <BalanceStat label="Gas capacity" value={balance.gas_capacity_kw} unit="kW" />
                  </div>
                ) : (
                  <p className="mt-6 text-sm text-[var(--text-subtle)]">
                    Live supply and demand appear here while the simulator runs.
                  </p>
                )}
                <p className="mt-6 text-sm leading-relaxed text-[var(--text-muted)]">
                  Solar farms, wind turbines, batteries, factories, and gas peakers
                  hold the balance every second. Where their curves cross, the price forms.
                </p>
              </div>
            </Reveal>
            <Reveal delay={160}>
              <div className="lg-glass h-full p-6">
                <CloudSun size={18} className="text-[var(--warning)]" />
                <h3 className="mt-4 text-lg font-semibold text-[var(--text)]">Weather-driven</h3>
                <p className="mt-2 text-sm leading-relaxed text-[var(--text-muted)]">
                  Coastal wind and rooftop solar follow live Open-Meteo weather,
                  not scripted curves.
                </p>
              </div>
            </Reveal>
            <Reveal delay={240}>
              <div className="lg-glass h-full p-6">
                <Zap size={18} className="text-[var(--success)]" />
                <h3 className="mt-4 text-lg font-semibold text-[var(--text)]">Anchored to CAISO</h3>
                <p className="mt-2 text-sm leading-relaxed text-[var(--text-muted)]">
                  {snapshot?.data_source?.summary ??
                    "Valuations calibrate against the real Californian grid price."}
                </p>
              </div>
            </Reveal>
          </div>
        </div>
      </section>

      {/* ------------------------------ TRADING ---------------------------- */}
      <section className="mx-auto grid w-full max-w-[1400px] grid-cols-1 items-center gap-12 px-4 py-24 md:px-8 lg:grid-cols-[0.95fr_1.05fr] lg:py-36">
        <Reveal className="order-2 lg:order-1">
          <div className="lg-glass overflow-hidden p-2">
            <div className="aspect-[16/11] overflow-hidden rounded-[14px]">
              <img
                src={marketShot}
                alt="The live EFlux market dashboard: merit order, price chart, and order book"
                className="h-full w-full object-cover object-[50%_62%]"
                loading="lazy"
              />
            </div>
          </div>
          <div className="lg-glass mt-4 px-5 py-4">
            <div className="text-xs font-medium text-[var(--text-subtle)]">Latest fills</div>
            <div className="mt-2 space-y-1.5 text-sm tabular-nums">
              {trades.length === 0 ? (
                <p className="text-[var(--text-subtle)]">Trades print here as the market clears.</p>
              ) : (
                trades.slice(0, 4).map((t) => (
                  <div key={tradeKey(t)} className="flex items-baseline justify-between gap-3">
                    <span className="truncate text-[var(--text-muted)]">
                      {t.kind === "trade"
                        ? `${nameOf(t.buy_vpp_id)} bought from ${nameOf(t.sell_vpp_id)}`
                        : `${nameOf(t.vpp_id)} settled with the grid`}
                    </span>
                    <span className="shrink-0 text-[var(--text)]">
                      {Number(t.price).toFixed(1)} <span className="text-xs text-[var(--text-subtle)]">$/MWh</span>
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>
        </Reveal>
        <Reveal delay={120} className="order-1 lg:order-2">
          <h2 className="text-4xl font-semibold tracking-tight text-[var(--text)] md:text-5xl">
            A market that keeps score.
          </h2>
          <p className="mt-6 max-w-xl text-lg leading-relaxed text-[var(--text-muted)]">
            A continuous double auction clears every trade at price-time priority.
            Results persist to a leaderboard that survives restarts, normalized so a
            bigger battery cannot simply buy the top spot.
          </p>
          <Link to="/leaderboard" className="lg-btn mt-8 h-11 px-6 text-sm font-medium">
            <Trophy size={16} className="text-[var(--warning)]" />
            View the leaderboard
          </Link>
        </Reveal>
      </section>

      {/* --------------------------- ENTRY TIERS --------------------------- */}
      <section className="mx-auto w-full max-w-[1400px] px-4 pb-28 md:px-8">
        <Reveal>
          <h2 className="text-4xl font-semibold tracking-tight text-[var(--text)] md:text-5xl">
            Choose how you enter.
          </h2>
        </Reveal>
        <div className="mt-12 grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-[1.5fr_1fr]">
          <Reveal delay={60}>
            <Link to="/market" className="lg-glass lg-glass-hover group relative block h-full min-h-[260px] overflow-hidden">
              <img
                src={participantsShot}
                alt="The EFlux participants roster"
                className="absolute inset-0 h-full w-full object-cover object-top opacity-50 transition-opacity duration-300 group-hover:opacity-60"
                loading="lazy"
              />
              <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(6,10,18,0.08),rgba(6,10,18,0.88))]" />
              <div className="relative flex h-full flex-col justify-end p-6">
                <h3 className="text-2xl font-semibold text-[var(--text)]">Watch</h3>
                <p className="mt-2 max-w-md text-sm leading-relaxed text-[var(--text-muted)]">
                  Spectate the live market: merit order, order book, and forty-plus
                  autonomous participants. No account needed.
                </p>
                <span className="mt-4 inline-flex items-center gap-1.5 text-sm font-medium text-[var(--accent)]">
                  Enter the market
                  <ArrowRight size={15} className="transition-transform group-hover:translate-x-1" />
                </span>
              </div>
            </Link>
          </Reveal>
          <Reveal delay={120}>
            <Link
              to="/vpps"
              className="lg-glass lg-glass-hover group block h-full p-6"
              style={{ background: "linear-gradient(135deg, rgba(34,183,232,0.16), rgba(10,18,32,0.4))" }}
            >
              <Bot size={20} className="text-[var(--accent)]" />
              <h3 className="mt-4 text-2xl font-semibold text-[var(--text)]">Managed agent</h3>
              <p className="mt-2 text-sm leading-relaxed text-[var(--text-muted)]">
                Pick an endowment, write a persona, choose the model. The platform
                runs the trading for you.
              </p>
              <span className="mt-4 inline-flex items-center gap-1.5 text-sm font-medium text-[var(--accent)]">
                Deploy an agent
                <ArrowRight size={15} className="transition-transform group-hover:translate-x-1" />
              </span>
            </Link>
          </Reveal>
          <Reveal delay={180}>
            <a
              href="/api/docs"
              target="_blank"
              rel="noreferrer"
              className="lg-glass lg-glass-hover group block h-full p-6"
            >
              <TerminalSquare size={20} className="text-[var(--text-muted)]" />
              <h3 className="mt-4 text-2xl font-semibold text-[var(--text)]">Your bot</h3>
              <p className="mt-2 text-sm leading-relaxed text-[var(--text-muted)]">
                Batch orders, idempotency keys, per-account rate limits, a Python SDK,
                and an MCP server. Bring anything that speaks HTTP.
              </p>
              <span className="mt-4 inline-flex items-center gap-1.5 text-sm font-medium text-[var(--accent)]">
                Open the API docs
                <ArrowRight size={15} className="transition-transform group-hover:translate-x-1" />
              </span>
            </a>
          </Reveal>
          <Reveal delay={240}>
            <Link to="/arena" className="lg-glass lg-glass-hover group block h-full p-6">
              <Swords size={20} className="text-[var(--violet)]" />
              <h3 className="mt-4 text-2xl font-semibold text-[var(--text)]">Model arena</h3>
              <p className="mt-2 text-sm leading-relaxed text-[var(--text-muted)]">
                Different LLMs steer identical power plants in the same market.
                See whose strategy actually holds up.
              </p>
              <span className="mt-4 inline-flex items-center gap-1.5 text-sm font-medium text-[var(--accent)]">
                Visit the arena
                <ArrowRight size={15} className="transition-transform group-hover:translate-x-1" />
              </span>
            </Link>
          </Reveal>
        </div>
      </section>

      {/* ------------------------------ FOOTER ----------------------------- */}
      <footer className="border-t border-[var(--border)]">
        <div className="mx-auto flex w-full max-w-[1400px] flex-col items-start justify-between gap-4 px-4 py-8 text-sm text-[var(--text-subtle)] md:flex-row md:items-center md:px-8">
          <div className="flex items-center gap-2.5">
            <BrandLogo size={22} />
            <span>
              Built at The University of Hong Kong by Zeyuan Niu, Qinghu Tang, and Yi Wang.
            </span>
          </div>
          <nav className="flex flex-wrap gap-5">
            <Link to="/market" className="hover:text-[var(--text)]">Market</Link>
            <Link to="/participants" className="hover:text-[var(--text)]">Participants</Link>
            <Link to="/leaderboard" className="hover:text-[var(--text)]">Leaderboard</Link>
            <Link to="/benchmarks" className="hover:text-[var(--text)]">Benchmarks</Link>
          </nav>
        </div>
      </footer>
    </div>
  );
}

function BalanceStat({ label, value, unit }: { label: string; value: number; unit: string }) {
  return (
    <div>
      <div className="text-2xl font-semibold tabular-nums text-[var(--text)] md:text-3xl">
        {value >= 100 ? Math.round(value) : value.toFixed(1)}
        <span className="ml-1 text-sm font-normal text-[var(--text-subtle)]">{unit}</span>
      </div>
      <div className="mt-1 text-xs text-[var(--text-muted)]">{label}</div>
    </div>
  );
}
