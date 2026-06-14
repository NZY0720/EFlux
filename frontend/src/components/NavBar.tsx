import { Link, useLocation } from "react-router-dom";

import { useAuth } from "../state/auth";
import { useMarket } from "../state/marketStream";
import BrandLogo from "./BrandLogo";
import { MarketIcon, ParticipantsIcon, VppIcon, type IconProps } from "./icons";

export default function NavBar() {
  const { email, logout } = useAuth();
  const { state: wsState } = useMarket();
  const loc = useLocation();

  const link = (to: string, label: string, Icon: (p: IconProps) => React.ReactElement) => {
    const active = loc.pathname === to;
    return (
      <Link
        to={to}
        className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition-colors ${
          active
            ? "bg-slate-800 text-white shadow-[inset_0_1px_0_0_rgba(255,255,255,0.06)]"
            : "text-slate-400 hover:bg-slate-800/60 hover:text-white"
        }`}
      >
        <Icon size={16} className={active ? "text-sky-400" : ""} />
        {label}
      </Link>
    );
  };

  const wsColor =
    wsState === "open" ? "bg-emerald-500" : wsState === "connecting" ? "bg-amber-500" : "bg-rose-500";

  return (
    <nav className="sticky top-0 z-20 flex items-center justify-between border-b border-slate-800/80 bg-slate-950/70 px-6 py-2.5 backdrop-blur-md">
      <div className="flex items-center gap-3">
        <Link to="/" className="flex items-center gap-2 pr-2">
          <BrandLogo size={30} />
          <span className="eflux-wordmark text-lg font-bold">EFlux</span>
        </Link>
        <div className="flex items-center gap-1">
          {link("/", "Market", MarketIcon)}
          {link("/participants", "Participants", ParticipantsIcon)}
          {link("/vpps", "My VPPs", VppIcon)}
        </div>
      </div>
      <div className="flex items-center gap-4 text-sm text-slate-400">
        <span className="flex items-center gap-1.5 rounded-full border border-slate-800 bg-slate-900/60 px-2.5 py-1">
          <span className={`inline-block h-2 w-2 rounded-full ${wsColor} ${wsState !== "open" ? "eflux-pulse" : ""}`} />
          <span className="text-xs tabular-nums">{wsState}</span>
        </span>
        {email ? (
          <>
            <span className="text-slate-300">{email}</span>
            <button onClick={logout} className="text-slate-400 transition-colors hover:text-white">
              Logout
            </button>
          </>
        ) : (
          <Link to="/login" className="text-slate-300 transition-colors hover:text-white">
            Login
          </Link>
        )}
      </div>
    </nav>
  );
}
