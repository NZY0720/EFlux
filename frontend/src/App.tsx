import { lazy, Suspense } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import ConnectionBanner from "./components/ConnectionBanner";
import NavBar from "./components/NavBar";
import { AuthProvider, useAuth } from "./state/auth";
import { MarketModeProvider } from "./state/marketMode";
import { MarketStreamProvider } from "./state/marketStream";
import { ThemeProvider } from "./state/theme";

const Arena = lazy(() => import("./pages/Arena"));
const Benchmarks = lazy(() => import("./pages/Benchmarks"));
const Competitions = lazy(() => import("./pages/Competitions"));
const CompetitionDetail = lazy(() => import("./pages/CompetitionDetail"));
const ForecastHub = lazy(() => import("./pages/ForecastHub"));
const Leaderboard = lazy(() => import("./pages/Leaderboard"));
const Login = lazy(() => import("./pages/Login"));
const MarketOverview = lazy(() => import("./pages/MarketOverview"));
const VppOverview = lazy(() => import("./pages/VppOverview"));
const VppDeploy = lazy(() => import("./pages/VppDeploy"));
const VppCockpit = lazy(() => import("./pages/VppCockpit"));
const DeveloperConsole = lazy(() => import("./pages/DeveloperConsole"));
const Participants = lazy(() => import("./pages/Participants"));
const ProveOut = lazy(() => import("./pages/ProveOut"));
const ProveOutRun = lazy(() => import("./pages/ProveOutRun"));
const WelcomePage = lazy(() => import("./pages/WelcomePage"));

function RouteFallback() {
  return (
    <div className="flex min-h-56 items-center justify-center" role="status" aria-label="Loading page">
      <span className="size-5 animate-spin rounded-full border-2 border-[var(--border-strong)] border-t-[var(--accent)] motion-reduce:animate-none" />
    </div>
  );
}

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
        <Suspense fallback={<RouteFallback />}>
          <Routes>
            <Route path="/" element={<WelcomePage />} />
            <Route path="/login" element={<Login />} />
            <Route path="/market" element={<MarketOverview />} />
            <Route path="/participants" element={<Participants />} />
            <Route path="/leaderboard" element={<Leaderboard />} />
            <Route path="/arena" element={<Arena />} />
            <Route path="/benchmarks" element={<Benchmarks />} />
            <Route path="/benchmarks/:runId" element={<Benchmarks />} />
            <Route path="/competitions" element={<Competitions />} />
            <Route path="/competitions/:slug" element={<CompetitionDetail />} />
            <Route path="/forecasts" element={<ForecastHub />} />
            <Route path="/prove-out" element={<RequireAuth><ProveOut /></RequireAuth>} />
            <Route path="/prove-out/runs/:id" element={<RequireAuth><ProveOutRun /></RequireAuth>} />
            <Route
              path="/vpps"
              element={
                <RequireAuth>
                  <VppOverview />
                </RequireAuth>
              }
            />
            <Route path="/vpps/new" element={<RequireAuth><VppDeploy /></RequireAuth>} />
            <Route path="/vpps/:id" element={<RequireAuth><VppCockpit /></RequireAuth>} />
            <Route path="/developer" element={<RequireAuth><DeveloperConsole /></RequireAuth>} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
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
