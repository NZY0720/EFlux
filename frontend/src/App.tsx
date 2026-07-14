import { lazy, Suspense } from "react";
import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";

import ConnectionBanner from "./components/ConnectionBanner";
import AgentDock from "./components/AgentDock";
import NavBar from "./components/NavBar";
import { AuthProvider, useAuth } from "./state/auth";
import { MarketModeProvider } from "./state/marketMode";
import { MarketStreamProvider } from "./state/marketStream";
import { ThemeProvider } from "./state/theme";

const AgentReleaseDetail = lazy(() => import("./pages/AgentReleaseDetail"));
const AgentReleases = lazy(() => import("./pages/AgentReleases"));
const BehaviorDatasetDetail = lazy(() => import("./pages/BehaviorDatasetDetail"));
const BehaviorDatasets = lazy(() => import("./pages/BehaviorDatasets"));
const Benchmarks = lazy(() => import("./pages/Benchmarks"));
const Competitions = lazy(() => import("./pages/Competitions"));
const CompetitionDetail = lazy(() => import("./pages/CompetitionDetail"));
const CompetitionSubmit = lazy(() => import("./pages/CompetitionSubmit"));
const ForecastHub = lazy(() => import("./pages/ForecastHub"));
const Leaderboard = lazy(() => import("./pages/Leaderboard"));
const Login = lazy(() => import("./pages/Login"));
const MarketOverview = lazy(() => import("./pages/MarketOverview"));
const VppOverview = lazy(() => import("./pages/VppOverview"));
const VppDeploy = lazy(() => import("./pages/VppDeploy"));
const VppCockpit = lazy(() => import("./pages/VppCockpit"));
const DeveloperConsole = lazy(() => import("./pages/DeveloperConsole"));
const EvaluationRuns = lazy(() => import("./pages/EvaluationRuns"));
const Participants = lazy(() => import("./pages/Participants"));
const ProveOut = lazy(() => import("./pages/ProveOut"));
const ProveOutRun = lazy(() => import("./pages/ProveOutRun"));
const WelcomePage = lazy(() => import("./pages/WelcomePage"));
const SubmissionStatus = lazy(() => import("./pages/SubmissionStatus"));

function RouteFallback() {
  return (
    <div className="flex min-h-56 items-center justify-center" role="status" aria-label="Loading page">
      <span className="size-5 animate-spin rounded-full border-2 border-[var(--border-strong)] border-t-[var(--accent)] motion-reduce:animate-none" />
    </div>
  );
}

function RequireAuth({ children }: { children: React.ReactElement }) {
  const { token, restoring } = useAuth();
  const loc = useLocation();
  if (restoring && !token) return <RouteFallback />;
  if (!token) return <Navigate to="/login" state={{ from: `${loc.pathname}${loc.search}` }} replace />;
  return children;
}

function EvaluateHome() {
  return <Navigate to="/evaluate/runs" replace />;
}

function LegacyDetailRedirect({ target }: { target: string }) {
  const params = useParams();
  const id = params.id ?? params.runId;
  return <Navigate to={id ? `${target}/${id}` : target} replace />;
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
            <Route path="/arena" element={<Navigate to="/leaderboard?view=llm" replace />} />
            <Route path="/evaluate" element={<EvaluateHome />} />
            <Route path="/evaluate/quick-test" element={<RequireAuth><ProveOut /></RequireAuth>} />
            <Route path="/evaluate/quick-test/runs/:id" element={<RequireAuth><ProveOutRun /></RequireAuth>} />
            <Route path="/evaluate/runs" element={<EvaluationRuns />} />
            <Route path="/evaluate/runs/:runId" element={<Benchmarks />} />
            <Route path="/agents" element={<AgentReleases />} />
            <Route path="/agents/releases/:id" element={<AgentReleaseDetail />} />
            <Route path="/agents/training-data" element={<BehaviorDatasets />} />
            <Route path="/agents/training-data/:id" element={<BehaviorDatasetDetail />} />
            <Route path="/benchmarks" element={<Navigate to="/evaluate/runs" replace />} />
            <Route path="/benchmarks/:runId" element={<LegacyDetailRedirect target="/evaluate/runs" />} />
            <Route path="/agent-releases" element={<Navigate to="/agents" replace />} />
            <Route path="/agent-releases/:id" element={<LegacyDetailRedirect target="/agents/releases" />} />
            <Route path="/behavior-datasets" element={<Navigate to="/agents/training-data" replace />} />
            <Route path="/behavior-datasets/:id" element={<LegacyDetailRedirect target="/agents/training-data" />} />
            <Route path="/competitions" element={<Competitions />} />
            <Route path="/competitions/:slug" element={<CompetitionDetail />} />
            <Route path="/competitions/:slug/submit" element={<RequireAuth><CompetitionSubmit /></RequireAuth>} />
            <Route path="/submissions/:id" element={<RequireAuth><SubmissionStatus /></RequireAuth>} />
            <Route path="/forecasts" element={<ForecastHub />} />
            <Route path="/prove-out" element={<Navigate to="/evaluate/quick-test" replace />} />
            <Route path="/prove-out/runs/:id" element={<LegacyDetailRedirect target="/evaluate/quick-test/runs" />} />
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
      {!isWelcome && <AgentDock />}
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
