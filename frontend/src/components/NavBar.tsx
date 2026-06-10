import { Link, useLocation } from "react-router-dom";

import { useAuth } from "../state/auth";
import { useMarket } from "../state/marketStream";

export default function NavBar() {
  const { email, logout } = useAuth();
  const { state: wsState } = useMarket();
  const loc = useLocation();

  const link = (to: string, label: string) => {
    const active = loc.pathname === to;
    return (
      <Link
        to={to}
        className={`px-3 py-1 rounded ${active ? "bg-slate-700 text-white" : "text-slate-300 hover:text-white"}`}
      >
        {label}
      </Link>
    );
  };

  const wsColor =
    wsState === "open" ? "bg-emerald-500" : wsState === "connecting" ? "bg-amber-500" : "bg-rose-500";

  return (
    <nav className="flex items-center justify-between px-6 py-3 border-b border-slate-800 bg-slate-900">
      <div className="flex items-center gap-4">
        <span className="text-lg font-semibold text-white">⚡ EFlux</span>
        {link("/", "Market")}
        {link("/vpps", "My VPPs")}
      </div>
      <div className="flex items-center gap-4 text-sm text-slate-400">
        <span className="flex items-center gap-1">
          <span className={`inline-block h-2 w-2 rounded-full ${wsColor}`}></span>
          <span>{wsState}</span>
        </span>
        {email ? (
          <>
            <span>{email}</span>
            <button onClick={logout} className="text-slate-400 hover:text-white">
              Logout
            </button>
          </>
        ) : (
          <Link to="/login" className="text-slate-300 hover:text-white">
            Login
          </Link>
        )}
      </div>
    </nav>
  );
}
