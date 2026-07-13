import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  Bot,
  CloudSun,
  FlaskConical,
  Gauge,
  MessagesSquare,
  TerminalSquare,
  Trophy,
  Zap,
} from "lucide-react";

import { fetchChatter, fetchMarketAgents, fetchMarketReflections } from "../api/client";
import type { ChatMessage, MarketEvent } from "../api/types";
import BrandLogo from "../components/BrandLogo";
import GridCanvas, { type GridCanvasHandle } from "../components/welcome/GridCanvas";
import { formatPrice } from "../lib/format";
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

function useHeroGlassPointer() {
  const ref = useRef<HTMLDivElement>(null);
  const rectRef = useRef<DOMRect | null>(null);
  const pointerRef = useRef({ x: 50, y: 34, active: false });
  const frameRef = useRef<number | null>(null);
  const reducedRef = useRef(false);

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => { reducedRef.current = media.matches; };
    update();
    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, []);

  useEffect(() => () => {
    if (frameRef.current !== null) window.cancelAnimationFrame(frameRef.current);
  }, []);

  const commit = () => {
    frameRef.current = null;
    const el = ref.current;
    if (!el) return;
    const { x, y, active } = pointerRef.current;
    el.style.setProperty("--lg-mx", `${x}%`);
    el.style.setProperty("--lg-my", `${y}%`);
    el.style.setProperty("--lg-pointer", active ? "1" : "0");
  };
  const schedule = () => {
    if (frameRef.current === null) frameRef.current = window.requestAnimationFrame(commit);
  };

  return {
    ref,
    onPointerEnter: () => { rectRef.current = ref.current?.getBoundingClientRect() ?? null; },
    onPointerMove: (event: React.PointerEvent<HTMLDivElement>) => {
      if (reducedRef.current) return;
      const rect = rectRef.current ?? ref.current?.getBoundingClientRect();
      if (!rect) return;
      pointerRef.current = {
        x: Math.max(0, Math.min(100, ((event.clientX - rect.left) / rect.width) * 100)),
        y: Math.max(0, Math.min(100, ((event.clientY - rect.top) / rect.height) * 100)),
        active: true,
      };
      schedule();
    },
    onPointerLeave: () => {
      if (reducedRef.current) return;
      pointerRef.current.active = false;
      schedule();
    },
  };
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
  const heroGlass = useHeroGlassPointer();
  const lastTradeRef = useRef<string | null>(null);
  const [roster, setRoster] = useState<{ name: string; category: string }[]>();
  const { chatter, rationale } = useAgentVoices();

  useEffect(() => {
    fetchMarketAgents()
      .then((agents) => setRoster(agents.map((a) => ({ name: a.name, category: a.category }))))
      .catch(() => {});
  }, []);

  // Dev-only hook so the canvas can be driven from the console (demos, debugging).
  useEffect(() => {
    if (import.meta.env.DEV) {
      (window as unknown as { __grid?: GridCanvasHandle | null }).__grid = canvasRef.current;
    }
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

  // The canvas follows the simulation's sun: solar nodes sleep when the sim says night.
  // Read the site-local hour straight from the sim_ts string (Date would re-zone it).
  useEffect(() => {
    const m = snapshot?.sim_ts?.match(/T(\d{2}):(\d{2})/);
    canvasRef.current?.setSimHour(m ? Number(m[1]) + Number(m[2]) / 60 : null);
  }, [snapshot?.sim_ts]);

  // Fresh chatroom lines surface as speech bubbles on the speaker's node.
  // Recency-gated (20s) so page-load history doesn't replay as a bubble burst.
  const seenChatRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    for (const m of chatter) {
      const key = `${m.name}|${m.wall_ts}`;
      if (seenChatRef.current.has(key)) continue;
      seenChatRef.current.add(key);
      if (Date.now() - Date.parse(m.wall_ts) < 20_000) {
        canvasRef.current?.speak(m.name, m.text);
      }
    }
    if (seenChatRef.current.size > 200) {
      seenChatRef.current = new Set([...seenChatRef.current].slice(-100));
    }
  }, [chatter]);

  const lastPrice = snapshot?.last_price ? Number(snapshot.last_price) : null;
  const live = wsState === "open" && snapshot !== null;
  const modeLabel = mode === "realprice" ? "Real-time price" : "Peer-to-peer";
  const balance = snapshot?.balance;
  const provenance = snapshot?.data_provenance ?? "synthetic";
  const provenanceClass = provenance === "real"
    ? "border-[color-mix(in_srgb,var(--success)_42%,transparent)] bg-[var(--success-soft)] text-[var(--success)]"
    : provenance === "cached"
      ? "border-[color-mix(in_srgb,var(--warning)_42%,transparent)] bg-[var(--warning-soft)] text-[var(--warning)]"
      : "border-[var(--border)] bg-[var(--surface-muted)] text-[var(--text-muted)]";

  return (
    <div className="wl-cinema min-h-screen">
      {/* ------------------------------ HERO ------------------------------ */}
      <section className="relative flex h-[100svh] min-h-[44rem] max-h-[100dvh] flex-col overflow-hidden">
        {/* Engineering-paper dot lattice, fading toward the copy side. */}
        <div
          className="pointer-events-none absolute inset-0 opacity-70"
          style={{
            backgroundImage: "radial-gradient(rgba(148,163,184,0.07) 1px, transparent 1.4px)",
            backgroundSize: "26px 26px",
            maskImage: "radial-gradient(120% 90% at 70% 40%, black 30%, transparent 78%)",
            WebkitMaskImage: "radial-gradient(120% 90% at 70% 40%, black 30%, transparent 78%)",
          }}
        />
        <GridCanvas ref={canvasRef} roster={roster} density={0.68} copyClearance={0.72} className="absolute inset-0" />
        {/* Legibility scrim behind the copy, transparent toward the living grid. */}
        <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(100deg,rgba(5,9,16,0.86)_0%,rgba(5,9,16,0.55)_38%,transparent_66%)]" />

        <header className="relative z-10 mx-auto flex w-full max-w-[1400px] items-center justify-between px-4 py-4 md:px-8">
          <Link to="/" className="flex items-center gap-2.5">
            <BrandLogo size={34} />
            <span className="eflux-wordmark text-xl font-bold">EFlux</span>
          </Link>
          <Link to="/login" className="eflux-btn h-10 px-5 text-sm font-medium">
            Sign in
          </Link>
        </header>

        <div className="relative z-10 mx-auto flex min-h-0 w-full max-w-[1400px] flex-1 items-center px-4 pb-24 md:px-8">
          <div className="max-w-xl">
            <h1 className="wl-hero-rise text-5xl font-semibold leading-[0.98] tracking-tight text-[var(--text)] sm:text-6xl lg:text-[clamp(3.5rem,5.2vw,4.5rem)]">
              The grid learns
              <br />
              to trade.
            </h1>
            <p
              className="wl-hero-rise mt-5 max-w-lg text-base leading-7 text-[var(--text-muted)] md:text-lg md:leading-8"
              style={{ animationDelay: "120ms" }}
            >
              Autonomous agents run virtual power plants inside a live electricity
              market. Watch them think, compete, and settle.
            </p>
            <div className="wl-hero-rise mt-7 flex flex-wrap gap-3" style={{ animationDelay: "240ms" }}>
              <Link to="/market" className="lg-glass wl-hero-primary eflux-btn eflux-btn-primary h-12 px-7 text-base">
                Enter the market
                <ArrowRight size={17} />
              </Link>
              <Link to="/vpps" className="eflux-btn h-12 px-7 text-base">
                Deploy an agent
              </Link>
            </div>
          </div>
        </div>

        {/* Live ticker: real numbers from the running simulator. */}
        <div className="wl-hero-rise relative z-10 mx-4 mb-6 md:absolute md:bottom-8 md:right-8 md:mx-0 md:mb-0" style={{ animationDelay: "360ms" }}>
          <div {...heroGlass} className="lg-glass wl-status-glass eflux-market-overlay flex items-center gap-5 px-5 py-3.5 text-sm">
            <span className="flex items-center gap-2">
              <span
                className={`inline-block h-2 w-2 rounded-full ${live ? "bg-[var(--success)] eflux-live-dot" : "bg-[var(--warning)]"}`}
              />
              <span className="font-medium text-[var(--text)]">
                {live ? "Market live" : "Market offline"}
              </span>
            </span>
            <span className="hidden text-[var(--text-subtle)] sm:inline">{modeLabel}</span>
            <span className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${provenanceClass}`}>{provenance}</span>
            {lastPrice !== null && (
              <span className="tabular-nums text-[var(--text)]">
                {formatPrice(lastPrice)}
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
          <div className="lg-frost eflux-panel flex min-h-[300px] flex-col p-6">
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
          <div className="grid grid-cols-1 items-end gap-12 lg:grid-cols-[1.1fr_0.9fr]">
            <Reveal>
              <div>
                <h2 className="max-w-2xl text-4xl font-semibold tracking-tight text-[var(--text)] md:text-5xl">
                  Real weather. Real prices. Real physics.
                </h2>
                <p className="mt-6 max-w-xl text-lg leading-relaxed text-[var(--text-muted)]">
                  Solar farms, wind turbines, batteries, factories, and gas peakers hold the balance every second.
                  Where their curves cross, the price forms.
                </p>
              </div>
            </Reveal>
            <Reveal delay={100}>
              <div className="lg-frost eflux-panel p-6 lg:translate-y-8">
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
              </div>
            </Reveal>
          </div>
          <div className="mt-12 grid grid-cols-1 gap-8 md:grid-cols-2 md:gap-12">
            <Reveal delay={160}>
              <div className="border-t border-[var(--border)] pt-5">
                <CloudSun size={18} className="text-[var(--warning)]" />
                <h3 className="mt-4 text-lg font-semibold text-[var(--text)]">Weather-driven</h3>
                <p className="mt-2 text-sm leading-relaxed text-[var(--text-muted)]">
                  Coastal wind and rooftop solar follow live Open-Meteo weather,
                  not scripted curves.
                </p>
              </div>
            </Reveal>
            <Reveal delay={240}>
              <div className="border-t border-[var(--border)] pt-5">
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
          <div className="lg-frost eflux-panel overflow-hidden p-2">
            <div className="aspect-[16/11] overflow-hidden rounded-[14px]">
              <img
                src={marketShot}
                alt="The live EFlux market dashboard: merit order, price chart, and order book"
                className="h-full w-full object-cover object-[50%_62%]"
                loading="lazy"
              />
            </div>
          </div>
          <div className="mt-4 border-t border-[var(--border)] px-1 pt-4">
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
                      {formatPrice(t.price)} <span className="text-xs text-[var(--text-subtle)]">$/MWh</span>
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
          <Link to="/leaderboard" className="eflux-btn mt-8 h-11 px-6 text-sm font-medium">
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
        <div className="mt-12 grid grid-cols-1 gap-10 lg:grid-cols-[1.15fr_0.85fr] lg:gap-16">
          <Reveal delay={60} className="lg-stagger">
            <Link to="/market" className="lg-frost lg-stagger-item eflux-card group relative block min-h-[320px] overflow-hidden">
              <img
                src={participantsShot}
                alt="The EFlux participants roster"
                className="absolute inset-0 h-full w-full object-cover object-top opacity-50 transition-opacity duration-200 group-hover:opacity-60"
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
                  <ArrowRight size={15} className="transition-transform duration-200 group-hover:translate-x-1" />
                </span>
              </div>
            </Link>
          </Reveal>
          <Reveal delay={120}>
            <div className="lg-stagger grid gap-3">
              <Link to="/vpps" className="lg-frost lg-stagger-item group block p-5">
                <div className="flex items-center gap-3"><Bot size={19} className="text-[var(--accent)]" /><h3 className="text-xl font-semibold text-[var(--text)]">Managed agent</h3></div>
                <p className="mt-2 text-sm leading-relaxed text-[var(--text-muted)]">Pick an endowment, write a persona, choose the model. The platform runs the trading for you.</p>
                <span className="mt-3 inline-flex items-center gap-1.5 text-sm font-medium text-[var(--accent)]">Deploy an agent <ArrowRight size={15} className="transition-transform duration-200 group-hover:translate-x-1" /></span>
              </Link>
              <a href="/api/docs" target="_blank" rel="noreferrer" className="lg-frost lg-stagger-item group block p-5">
                <div className="flex items-center gap-3"><TerminalSquare size={19} className="text-[var(--text-muted)]" /><h3 className="text-xl font-semibold text-[var(--text)]">Your bot</h3></div>
                <p className="mt-2 text-sm leading-relaxed text-[var(--text-muted)]">Batch orders, idempotency keys, per-account rate limits, a Python SDK, and an MCP server.</p>
                <span className="mt-3 inline-flex items-center gap-1.5 text-sm font-medium text-[var(--accent)]">Open the API docs <ArrowRight size={15} className="transition-transform duration-200 group-hover:translate-x-1" /></span>
              </a>
              <Link to="/evaluate" className="lg-frost lg-stagger-item group block p-5">
                <div className="flex items-center gap-3"><FlaskConical size={19} className="text-[var(--violet)]" /><h3 className="text-xl font-semibold text-[var(--text)]">Evaluate a strategy</h3></div>
                <p className="mt-2 text-sm leading-relaxed text-[var(--text-muted)]">Run a private historical test, then compare the result with reproducible reference runs.</p>
                <span className="mt-3 inline-flex items-center gap-1.5 text-sm font-medium text-[var(--accent)]">Open Evaluate <ArrowRight size={15} className="transition-transform duration-200 group-hover:translate-x-1" /></span>
              </Link>
            </div>
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
            <Link to="/evaluate" className="hover:text-[var(--text)]">Evaluate</Link>
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
