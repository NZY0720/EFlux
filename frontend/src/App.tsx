import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import ConnectionBanner from "./components/ConnectionBanner";
import NavBar from "./components/NavBar";
import Arena from "./pages/Arena";
import Benchmarks from "./pages/Benchmarks";
import Leaderboard from "./pages/Leaderboard";
import Login from "./pages/Login";
import MarketOverview from "./pages/MarketOverview";
import MyVPPs from "./pages/MyVPPs";
import Participants from "./pages/Participants";
import WelcomePage from "./pages/WelcomePage";
import { AuthProvider, useAuth } from "./state/auth";
import { MarketModeProvider } from "./state/marketMode";
import { MarketStreamProvider } from "./state/marketStream";
import { ThemeProvider } from "./state/theme";

function RequireAuth({ children }: { children: React.ReactElement }) {
  const { token } = useAuth();
  const loc = useLocation();
  if (!token) return <Navigate to="/login" state={{ from: `${loc.pathname}${loc.search}` }} replace />;
  return children;
}

function Shell() {
  const loc = useLocation();
  const isWelcome = loc.pathname === "/";

  return (
    <div className="min-h-screen flex flex-col">
      {!isWelcome && <NavBar />}
      {!isWelcome && <ConnectionBanner />}
      <main className="flex-1">
        <Routes>
          <Route path="/" element={<WelcomePage />} />
          <Route path="/login" element={<Login />} />
          <Route path="/market" element={<MarketOverview />} />
          <Route path="/participants" element={<Participants />} />
          <Route path="/leaderboard" element={<Leaderboard />} />
          <Route path="/arena" element={<Arena />} />
          <Route path="/benchmarks" element={<Benchmarks />} />
          <Route path="/benchmarks/:runId" element={<Benchmarks />} />
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
    <ThemeProvider>
      <AuthProvider>
        <MarketModeProvider>
          <MarketStreamProvider>
            <Shell />
          </MarketStreamProvider>
        </MarketModeProvider>
      </AuthProvider>
    </ThemeProvider>
  );
}
