import { Link } from "react-router-dom";
import {
  Activity,
  ArrowRight,
  BarChart3,
  Bot,
  Cable,
  Gauge,
  KeyRound,
  Layers3,
  LineChart,
  Moon,
  ShieldCheck,
  Sun,
  type LucideIcon,
  Zap,
} from "lucide-react";

import BrandLogo from "../components/BrandLogo";
import { useTheme } from "../state/theme";
import marketShot from "../../../docs/img/market.png";
import participantsShot from "../../../docs/img/participants.png";

const MARKET_CARDS = [
  {
    title: "P2P Market",
    label: "Continuous double auction",
    body: "Autonomous VPPs negotiate with each other. Local prices emerge from bids, asks, liquidity and agent strategy.",
    to: "/market?target=p2p",
    icon: Layers3,
    tone: "accent",
  },
  {
    title: "Real-Time Price Market",
    label: "CAISO price-taking testbed",
    body: "Agents settle against the live grid price, making strategy timing, PnL and risk behavior easy to compare.",
    to: "/market?target=realprice",
    icon: LineChart,
    tone: "amber",
  },
] as const;

const CAPABILITIES = [
  {
    icon: Bot,
    title: "LLM-steered agents",
    body: "Hybrid policies combine structured trading primitives, PPO execution and slow strategic reflection.",
  },
  {
    icon: BarChart3,
    title: "Live market telemetry",
    body: "Order flow, prices, PnL, depth and participant state update continuously during the simulation.",
  },
  {
    icon: ShieldCheck,
    title: "Research-ready controls",
    body: "Scenario files, auth-gated VPP creation and API endpoints make experiments repeatable.",
  },
] as const;

export default function WelcomePage() {
  const { mode, toggleMode } = useTheme();
  const ThemeIcon = mode === "dark" ? Sun : Moon;

  return (
    <div className="min-h-screen overflow-hidden bg-[var(--bg)] text-[var(--text)]">
      <section className="relative min-h-[86svh] overflow-hidden border-b border-[var(--border)] lg:min-h-[92svh]">
        <img
          src={marketShot}
          alt="EFlux live market dashboard"
          className="absolute inset-0 h-full w-full object-cover"
        />
        <div className="absolute inset-0 bg-[rgba(2,6,23,0.72)]" />
        <div className="absolute inset-0 bg-[color-mix(in_srgb,var(--accent)_18%,transparent)] mix-blend-screen" />

        <header className="relative z-10 mx-auto flex w-full max-w-[1380px] items-center justify-between gap-4 px-4 py-5 md:px-6">
          <Link to="/" className="flex min-w-0 items-center gap-2">
            <BrandLogo size={34} />
            <span className="eflux-wordmark text-xl font-bold">EFlux</span>
          </Link>
          <div className="flex items-center gap-2">
            <Link to="/market" className="eflux-btn hidden h-9 px-3 text-sm sm:inline-flex">
              <Activity size={15} />
              Market
            </Link>
            <Link to="/login?mode=register" className="eflux-btn hidden h-9 px-3 text-sm sm:inline-flex">
              <KeyRound size={15} />
              Register
            </Link>
            <Link to="/login" className="eflux-btn eflux-btn-primary h-9 px-3 text-sm font-semibold">
              Login
            </Link>
            <button
              type="button"
              onClick={toggleMode}
              title={mode === "dark" ? "Switch to light theme" : "Switch to dark theme"}
              aria-label={mode === "dark" ? "Switch to light theme" : "Switch to dark theme"}
              className="eflux-btn h-9 w-9 p-0"
            >
              <ThemeIcon size={16} />
            </button>
          </div>
        </header>

        <div className="relative z-10 mx-auto grid w-full max-w-[1380px] grid-cols-1 items-center gap-10 px-4 pb-10 pt-8 md:px-6 lg:grid-cols-[minmax(0,0.94fr)_minmax(420px,0.68fr)] lg:pb-20 lg:pt-16">
          <div className="max-w-4xl">
            <div className="mb-5 inline-flex items-center gap-2 rounded-full border border-white/20 bg-black/24 px-3 py-1 text-xs font-semibold uppercase text-sky-100 backdrop-blur-md">
              <Zap size={14} className="text-[var(--success)]" />
              Agent-based VPP electricity trading platform
            </div>
            <h1 className="max-w-4xl text-5xl font-semibold leading-[1.02] text-white sm:text-6xl lg:text-7xl">
              EFlux
            </h1>
            <p className="mt-6 max-w-3xl text-xl leading-8 text-slate-100 md:text-2xl md:leading-9">
              A live electricity market laboratory where autonomous virtual power plants learn,
              compete and trade across peer-to-peer and real-time price settings.
            </p>
            <p className="mt-5 max-w-2xl text-sm leading-6 text-slate-300 md:text-base">
              Developed by Zeyuan Niu, Qinghu Tang, and Yi Wang @HKU.
            </p>

            <div className="mt-8 flex flex-wrap gap-3">
              <Link to="/market" className="eflux-btn eflux-btn-primary h-11 px-5 text-sm font-semibold">
                Enter live market
                <ArrowRight size={16} />
              </Link>
              <Link to="/login?mode=register" className="eflux-btn h-11 px-5 text-sm font-semibold">
                Register
              </Link>
              <Link to="/login" className="eflux-btn h-11 px-5 text-sm font-semibold">
                Login
              </Link>
            </div>

            <dl className="mt-10 hidden max-w-2xl grid-cols-3 gap-3 sm:grid">
              <Metric value="33" label="built-in VPPs" />
              <Metric value="2s" label="market refresh" />
              <Metric value="PPO + LLM" label="hybrid agents" />
            </dl>
          </div>

          <div className="relative hidden min-h-[480px] lg:block">
            <div className="absolute right-0 top-0 w-[94%] overflow-hidden rounded-lg border border-white/18 bg-slate-950/72 shadow-2xl shadow-sky-950/50">
              <img src={marketShot} alt="EFlux market overview" className="h-auto w-full" />
            </div>
            <div className="absolute bottom-0 left-0 w-[74%] overflow-hidden rounded-lg border border-white/18 bg-slate-950/72 shadow-2xl shadow-emerald-950/40">
              <img src={participantsShot} alt="EFlux participants roster" className="h-auto w-full" />
            </div>
          </div>
        </div>
      </section>

      <main className="relative z-10">
        <section className="mx-auto grid w-full max-w-[1380px] grid-cols-1 gap-4 px-4 py-8 md:px-6 lg:grid-cols-2">
          {MARKET_CARDS.map((market) => (
            <MarketCard key={market.title} {...market} />
          ))}
        </section>

        <section className="mx-auto grid w-full max-w-[1380px] grid-cols-1 gap-5 px-4 py-8 md:px-6 lg:grid-cols-[minmax(0,0.9fr)_minmax(380px,0.6fr)]">
          <div className="space-y-5">
            <div>
              <p className="text-sm font-semibold uppercase text-[var(--accent)]">Project overview</p>
              <h2 className="mt-2 max-w-3xl text-3xl font-semibold text-[var(--text)] md:text-4xl">
                A controllable market lab for electricity trading agents.
              </h2>
            </div>
            <p className="max-w-3xl text-base leading-7 text-[var(--text-muted)]">
              EFlux connects a FastAPI simulator, a continuous double auction engine, live VPP
              state, passwordless participation and a React dashboard. It is built for demos,
              experiments and agent strategy comparisons rather than a static market replay.
            </p>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
              {CAPABILITIES.map((item) => (
                <Capability key={item.title} {...item} />
              ))}
            </div>
          </div>

          <aside className="eflux-card p-5">
            <div className="flex items-center gap-3">
              <BrandLogo size={38} />
              <div>
                <h3 className="text-lg font-semibold text-[var(--text)]">Built for live demonstrations</h3>
                <p className="text-sm text-[var(--text-muted)]">The dashboard is the interface, not a slide deck.</p>
              </div>
            </div>
            <div className="mt-5 space-y-3 text-sm text-[var(--text-muted)]">
              <Feature icon={Gauge} text="Watch price formation, order depth and strategy PnL evolve in real time." />
              <Feature icon={Cable} text="Connect external or managed VPPs through authenticated endpoints." />
              <Feature icon={Activity} text="Use the welcome screen as the launch point for both market tracks." />
            </div>
            <div className="mt-6 flex flex-wrap gap-3">
              <Link to="/market" className="eflux-btn eflux-btn-primary h-10 px-4 text-sm font-semibold">
                Open market
              </Link>
              <Link to="/participants" className="eflux-btn h-10 px-4 text-sm font-semibold">
                View participants
              </Link>
            </div>
          </aside>
        </section>

        <div className="pb-8" />
      </main>
    </div>
  );
}

function Metric({ value, label }: { value: string; label: string }) {
  return (
    <div className="rounded-lg border border-white/16 bg-black/24 px-3 py-3 backdrop-blur-md">
      <dt className="text-lg font-semibold text-white">{value}</dt>
      <dd className="mt-1 text-xs uppercase text-slate-300">{label}</dd>
    </div>
  );
}

function MarketCard({
  title,
  label,
  body,
  to,
  icon: Icon,
  tone,
}: (typeof MARKET_CARDS)[number]) {
  const toneClass =
    tone === "amber"
      ? "text-[var(--warning)] bg-[var(--warning-soft)] border-[color-mix(in_srgb,var(--warning)_42%,transparent)]"
      : "text-[var(--accent)] bg-[var(--accent-soft)] border-[color-mix(in_srgb,var(--accent)_42%,transparent)]";

  return (
    <Link to={to} className="eflux-card group block p-5">
      <div className="flex items-start justify-between gap-4">
        <div className={`rounded-lg border p-3 ${toneClass}`}>
          <Icon size={22} />
        </div>
        <ArrowRight size={18} className="mt-2 text-[var(--text-subtle)] transition-transform group-hover:translate-x-1 group-hover:text-[var(--text)]" />
      </div>
      <p className="mt-5 text-xs font-semibold uppercase text-[var(--text-subtle)]">{label}</p>
      <h2 className="mt-2 text-2xl font-semibold text-[var(--text)]">{title}</h2>
      <p className="mt-3 max-w-2xl text-sm leading-6 text-[var(--text-muted)]">{body}</p>
      <div className="mt-5 text-sm font-semibold text-[var(--accent)]">
        Explore market track
      </div>
    </Link>
  );
}

function Capability({
  icon: Icon,
  title,
  body,
}: (typeof CAPABILITIES)[number]) {
  return (
    <article className="eflux-card p-4">
      <Icon size={20} className="text-[var(--accent)]" />
      <h3 className="mt-4 text-base font-semibold text-[var(--text)]">{title}</h3>
      <p className="mt-2 text-sm leading-6 text-[var(--text-muted)]">{body}</p>
    </article>
  );
}

function Feature({ icon: Icon, text }: { icon: LucideIcon; text: string }) {
  return (
    <div className="flex gap-3">
      <Icon size={17} className="mt-0.5 shrink-0 text-[var(--accent)]" />
      <p className="leading-6">{text}</p>
    </div>
  );
}
