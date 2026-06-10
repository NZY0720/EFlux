import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import NavBar from "./components/NavBar";
import Login from "./pages/Login";
import MarketOverview from "./pages/MarketOverview";
import MyVPPs from "./pages/MyVPPs";
import { AuthProvider, useAuth } from "./state/auth";
import type { ConnectionState } from "./ws/useMarketStream";

function RequireAuth({ children }: { children: React.ReactElement }) {
  const { token } = useAuth();
  const loc = useLocation();
  if (!token) return <Navigate to="/login" state={{ from: loc.pathname }} replace />;
  return children;
}

function Shell() {
  // Lift WS state from MarketOverview via a window-level event (small cross-cut).
  const [wsState, setWsState] = useState<ConnectionState | undefined>(undefined);
  useEffect(() => {
    const handler = (e: Event) => setWsState((e as CustomEvent<ConnectionState>).detail);
    window.addEventListener("eflux:ws-state", handler as EventListener);
    return () => window.removeEventListener("eflux:ws-state", handler as EventListener);
  }, []);

  return (
    <div className="min-h-screen flex flex-col">
      <NavBar wsState={wsState} />
      <main className="flex-1">
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/" element={<MarketOverview />} />
          <Route
            path="/vpps"
            element={
              <RequireAuth>
                <MyVPPs />
              </RequireAuth>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <Shell />
    </AuthProvider>
  );
}
