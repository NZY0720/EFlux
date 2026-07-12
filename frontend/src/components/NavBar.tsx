import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Link, useLocation } from "react-router-dom";
import { Activity, ChartNoAxesCombined, ChevronDown, FlaskConical, Gauge, LoaderCircle, LogIn, LogOut, Menu, Moon, Sun, Swords, Terminal, Trophy, UsersRound, Wifi, WifiOff, X, type LucideIcon } from "lucide-react";

import { useAuth } from "../state/auth";
import { useMarketMode } from "../state/marketMode";
import { useMarket } from "../state/marketStream";
import { useTheme } from "../state/theme";
import BrandLogo from "./BrandLogo";

type NavItem = { to: string; label: string; icon: LucideIcon };

const PRIMARY_ITEMS: NavItem[] = [
  { to: "/market", label: "Live Market", icon: Activity },
  { to: "/leaderboard", label: "Leaderboard", icon: Trophy },
];
// Signed-in users get their control center in the primary nav — the IA rework
// briefly dropped it (2026-07-10) and the app became unreachable-by-click.
const MY_VPPS_ITEM: NavItem = { to: "/vpps", label: "My VPPs", icon: Gauge };
const PROVE_OUT_ITEM: NavItem = { to: "/prove-out", label: "Prove-out", icon: FlaskConical };
const EXPLORE_ITEMS: NavItem[] = [
  { to: "/arena", label: "Arena", icon: Swords },
  { to: "/competitions", label: "Compete", icon: Trophy },
  { to: "/participants", label: "Participants", icon: UsersRound },
  { to: "/benchmarks", label: "Benchmarks", icon: FlaskConical },
  { to: "/forecasts", label: "Forecasts", icon: ChartNoAxesCombined },
  { to: "/developer", label: "Developer", icon: Terminal },
];

function focusableElements(container: HTMLElement): HTMLElement[] {
  return [...container.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])')]
    .filter((element) => !element.hasAttribute("aria-hidden"));
}

export default function NavBar() {
  const { email, logout } = useAuth();
  const { state: wsState } = useMarket();
  const { mode } = useMarketMode();
  const { mode: themeMode, toggleMode } = useTheme();
  const loc = useLocation();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerMounted, setDrawerMounted] = useState(false);
  const [exploreOpen, setExploreOpen] = useState(false);
  const drawerRef = useRef<HTMLDivElement>(null);
  const drawerTriggerRef = useRef<HTMLButtonElement>(null);
  const exploreRef = useRef<HTMLDivElement>(null);

  const openDrawer = () => {
    setDrawerMounted(true);
    window.requestAnimationFrame(() => setDrawerOpen(true));
  };
  const closeDrawer = () => {
    setDrawerOpen(false);
    window.setTimeout(() => {
      setDrawerMounted(false);
      drawerTriggerRef.current?.focus();
    }, 200);
  };
  useEffect(() => {
    if (!drawerOpen) return;
    const drawer = drawerRef.current;
    focusableElements(drawer ?? document.body)[0]?.focus();
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); closeDrawer(); return; }
      if (event.key !== "Tab" || !drawer) return;
      const elements = focusableElements(drawer);
      if (!elements.length) return;
      const first = elements[0];
      const last = elements[elements.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [drawerOpen]);
  useEffect(() => {
    const onPointerDown = (event: MouseEvent) => {
      if (exploreRef.current && !exploreRef.current.contains(event.target as Node)) setExploreOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, []);
  useEffect(() => { setDrawerOpen(false); setExploreOpen(false); }, [loc.pathname]);

  const modeLabel = mode === "realprice" ? "Real-Time Price" : "P2P Market";
  const modeClass = mode === "realprice" ? "border-[color-mix(in_srgb,var(--warning)_46%,transparent)] bg-[var(--warning-soft)] text-[var(--warning)]" : "border-[color-mix(in_srgb,var(--accent)_46%,transparent)] bg-[var(--accent-soft)] text-[var(--accent)]";
  const wsTone = wsState === "open" ? "text-[var(--success)]" : wsState === "connecting" ? "text-[var(--warning)]" : "text-[var(--danger)]";
  const WsIcon = wsState === "open" ? Wifi : wsState === "connecting" ? LoaderCircle : WifiOff;
  const ThemeIcon = themeMode === "dark" ? Sun : Moon;
  const primaryItems = email ? [...PRIMARY_ITEMS, MY_VPPS_ITEM, PROVE_OUT_ITEM] : PRIMARY_ITEMS;
  const exploreActive = EXPLORE_ITEMS.some((item) => loc.pathname === item.to || loc.pathname.startsWith(`${item.to}/`));

  const navLink = (item: NavItem, mobile = false) => {
    const Icon = item.icon;
    const active = loc.pathname === item.to || (item.to === "/competitions" && loc.pathname.startsWith("/competitions/"));
    return <Link key={item.to} to={item.to} onClick={mobile ? closeDrawer : undefined} className={`${mobile ? "h-11 w-full" : "h-9 whitespace-nowrap"} flex items-center gap-2 rounded-md px-3 text-sm font-medium transition-colors ${active ? "eflux-tab-active" : "text-[var(--text-muted)] hover:bg-[var(--surface-hover)] hover:text-[var(--text)]"}`}><Icon size={16} className={active ? "text-[var(--accent)]" : ""} />{item.label}</Link>;
  };
  const closeExploreOnEscape = (event: KeyboardEvent<HTMLButtonElement>) => { if (event.key === "Escape") setExploreOpen(false); };

  return <nav className="lg-glass eflux-nav sticky top-0 z-20 border-b border-[var(--border)] px-4 py-2.5 md:px-6">
    <div className="flex items-center justify-between gap-3">
      <div className="flex min-w-0 items-center gap-3">
        <Link to="/" className="flex shrink-0 items-center gap-2 pr-1"><BrandLogo size={30} /><span className="eflux-wordmark text-lg font-bold">EFlux</span></Link>
        <span className={`hidden rounded-full border px-2.5 py-1 text-xs font-semibold sm:inline ${modeClass}`}>{modeLabel}</span>
        <div className="hidden items-center gap-1 lg:flex">
          {primaryItems.map((item) => navLink(item))}
          <div className="relative" ref={exploreRef}>
            <button type="button" onClick={() => setExploreOpen((open) => !open)} onKeyDown={closeExploreOnEscape} aria-expanded={exploreOpen} aria-haspopup="menu" aria-controls="explore-menu" className={`flex h-9 items-center gap-1.5 rounded-md px-3 text-sm font-medium transition-colors ${exploreActive ? "eflux-tab-active" : "text-[var(--text-muted)] hover:bg-[var(--surface-hover)] hover:text-[var(--text)]"}`}>Explore <ChevronDown size={15} className={`transition-transform duration-200 motion-reduce:transition-none ${exploreOpen ? "rotate-180" : ""}`} /></button>
            {exploreOpen && <div id="explore-menu" role="menu" aria-label="Explore" className="lg-glass absolute right-0 top-full mt-2 w-48 rounded-lg border border-[var(--border)] p-1.5 shadow-lg">{EXPLORE_ITEMS.map((item) => <div role="none" key={item.to}>{navLink(item)}</div>)}</div>}
          </div>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-2 text-sm text-[var(--text-muted)]">
        <span className="hidden h-9 items-center gap-1.5 rounded-full border border-[var(--border)] bg-[var(--surface-muted)] px-2.5 sm:flex"><span className={`relative inline-flex h-2 w-2 rounded-full ${wsTone} ${wsState === "open" ? "eflux-live-dot" : "eflux-pulse"}`}><span className="h-2 w-2 rounded-full bg-current" /></span><WsIcon size={14} className={`${wsTone} ${wsState === "connecting" ? "animate-spin" : ""}`} /><span className="text-xs tabular-nums">{wsState}</span></span>
        <button type="button" onClick={toggleMode} title={themeMode === "dark" ? "Switch to light theme" : "Switch to dark theme"} aria-label={themeMode === "dark" ? "Switch to light theme" : "Switch to dark theme"} className="eflux-btn h-9 w-9 p-0"><ThemeIcon size={16} /></button>
        {email ? <><span className="hidden max-w-[220px] truncate text-[var(--text)] xl:inline">{email}</span><button onClick={logout} className="eflux-btn h-9 px-3"><LogOut size={15} /><span className="hidden sm:inline">Logout</span></button></> : <Link to="/login" className="eflux-btn h-9 px-3"><LogIn size={15} /><span className="hidden sm:inline">Login</span></Link>}
        <button ref={drawerTriggerRef} type="button" onClick={openDrawer} aria-expanded={drawerOpen} aria-controls="mobile-nav-drawer" aria-label="Open navigation menu" className="eflux-btn h-9 w-9 p-0 lg:hidden"><Menu size={18} /></button>
      </div>
    </div>
    {drawerMounted && <div className="fixed inset-0 z-50 lg:hidden" aria-hidden={!drawerOpen}><button type="button" aria-label="Close navigation menu" onClick={closeDrawer} className={`absolute inset-0 bg-black/45 transition-opacity duration-200 motion-reduce:transition-none ${drawerOpen ? "opacity-100" : "opacity-0"}`} /><div id="mobile-nav-drawer" ref={drawerRef} role="dialog" aria-modal="true" aria-label="Navigation menu" className={`lg-glass absolute inset-y-0 left-0 flex w-[min(20rem,calc(100vw-2rem))] flex-col border-r border-[var(--border)] p-4 shadow-xl transition-transform duration-200 ease-out motion-reduce:transition-none ${drawerOpen ? "translate-x-0" : "-translate-x-full"}`}><div className="flex items-center justify-between"><span className="eflux-wordmark text-lg font-bold">Navigation</span><button type="button" onClick={closeDrawer} aria-label="Close navigation menu" className="eflux-btn h-9 w-9 p-0"><X size={18} /></button></div><div className="mt-5 space-y-1"><p className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-wide text-[var(--text-subtle)]">Main</p>{primaryItems.map((item) => navLink(item, true))}<p className="px-3 pb-1 pt-4 text-[11px] font-semibold uppercase tracking-wide text-[var(--text-subtle)]">Explore</p>{EXPLORE_ITEMS.map((item) => navLink(item, true))}</div><div className="mt-auto border-t border-[var(--border)] pt-4"><span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-semibold ${modeClass}`}>{modeLabel}</span></div></div></div>}
  </nav>;
}
