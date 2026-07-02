import { Link, useLocation } from "react-router-dom";
import {
  Activity,
  FlaskConical,
  Layers3,
  LoaderCircle,
  LogIn,
  LogOut,
  Moon,
  Sun,
  Swords,
  Trophy,
  UsersRound,
  Wifi,
  WifiOff,
  type LucideIcon,
} from "lucide-react";

import { useAuth } from "../state/auth";
import { useMarketMode } from "../state/marketMode";
import { useMarket } from "../state/marketStream";
import { useTheme } from "../state/theme";
import BrandLogo from "./BrandLogo";

export default function NavBar() {
  const { email, logout } = useAuth();
  const { state: wsState } = useMarket();
  const { mode } = useMarketMode();
  const { mode: themeMode, toggleMode } = useTheme();
  const loc = useLocation();

  const modeLabel = mode === "realprice" ? "Real-Time Price" : "P2P Market";
  const modeClass =
    mode === "realprice"
      ? "border-[color-mix(in_srgb,var(--warning)_46%,transparent)] bg-[var(--warning-soft)] text-[var(--warning)]"
      : "border-[color-mix(in_srgb,var(--accent)_46%,transparent)] bg-[var(--accent-soft)] text-[var(--accent)]";

  const link = (to: string, label: string, Icon: LucideIcon) => {
    const active = loc.pathname === to;
    return (
      <Link
        to={to}
        className={`flex h-9 items-center gap-1.5 rounded-md px-3 text-sm font-medium transition-colors ${
          active
            ? "eflux-tab-active"
            : "text-[var(--text-muted)] hover:bg-[var(--surface-hover)] hover:text-[var(--text)]"
        }`}
      >
        <Icon size={16} className={active ? "text-[var(--accent)]" : ""} />
        {label}
      </Link>
    );
  };

  const wsTone =
    wsState === "open"
      ? "text-[var(--success)]"
      : wsState === "connecting"
        ? "text-[var(--warning)]"
        : "text-[var(--danger)]";
  const WsIcon = wsState === "open" ? Wifi : wsState === "connecting" ? LoaderCircle : WifiOff;
  const ThemeIcon = themeMode === "dark" ? Sun : Moon;

  return (
    <nav className="sticky top-0 z-20 border-b border-[var(--border)] bg-[color-mix(in_srgb,var(--bg-elevated)_72%,transparent)] px-4 py-2.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.06)] backdrop-blur-2xl backdrop-saturate-150 md:px-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-3">
          <Link to="/" className="flex items-center gap-2 pr-1">
            <BrandLogo size={30} />
            <span className="eflux-wordmark text-lg font-bold">EFlux</span>
          </Link>
          <span className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${modeClass}`}>{modeLabel}</span>
          <div className="flex items-center gap-1 overflow-x-auto">
            {link("/market", "Market", Activity)}
            {link("/participants", "Participants", UsersRound)}
            {link("/leaderboard", "Leaderboard", Trophy)}
            {link("/arena", "Arena", Swords)}
            {link("/benchmarks", "Benchmarks", FlaskConical)}
            {link("/vpps", "My VPPs", Layers3)}
          </div>
        </div>
        <div className="flex items-center gap-2 text-sm text-[var(--text-muted)]">
          <span className="flex h-9 items-center gap-1.5 rounded-full border border-[var(--border)] bg-[var(--surface-muted)] px-2.5">
            <span className={`relative inline-flex h-2 w-2 rounded-full ${wsTone} ${wsState === "open" ? "eflux-live-dot" : "eflux-pulse"}`}>
              <span className="h-2 w-2 rounded-full bg-current" />
            </span>
            <WsIcon size={14} className={`${wsTone} ${wsState === "connecting" ? "animate-spin" : ""}`} />
            <span className="text-xs tabular-nums">{wsState}</span>
          </span>
          <button
            type="button"
            onClick={toggleMode}
            title={themeMode === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            aria-label={themeMode === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            className="eflux-btn h-9 w-9 p-0"
          >
            <ThemeIcon size={16} />
          </button>
          {email ? (
            <>
              <span className="hidden max-w-[220px] truncate text-[var(--text)] sm:inline">{email}</span>
              <button onClick={logout} className="eflux-btn h-9 px-3">
                <LogOut size={15} />
                <span className="hidden sm:inline">Logout</span>
              </button>
            </>
          ) : (
            <Link to="/login" className="eflux-btn h-9 px-3">
              <LogIn size={15} />
              Login
            </Link>
          )}
        </div>
      </div>
    </nav>
  );
}
