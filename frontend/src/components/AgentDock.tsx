import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Bot,
  CheckCircle2,
  Clock3,
  ExternalLink,
  Gauge,
  MessageSquareText,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import { Link, useLocation } from "react-router-dom";

import {
  fetchManagedVPPPerformance,
  listManagedVPPs,
  putGuidance,
  releaseGuidance,
  type GuidancePayload,
} from "../api/client";
import type {
  ManagedVPP,
  ManagedVPPPerformance,
  MarketEvent,
  MarketSnapshot,
  ReflectionEntry,
} from "../api/types";
import { useAuth } from "../state/auth";
import { useMarket } from "../state/marketStream";

type DockTab = "activity" | "decision";
type PlanAction = "guidance" | "release";

interface ComplexityAssessment {
  score: number;
  reasons: string[];
}

interface TimelineItem {
  id: string;
  ts: number;
  kind: "trade" | "guidance" | "error";
  label: string;
  summary: string;
  detail?: string;
}

interface StrategyPlan {
  id: string;
  title: string;
  score: number | null;
  risk: "low" | "medium" | "high";
  summary: string;
  tradeoff: string;
  changes: string[];
  action: PlanAction;
  payload?: GuidancePayload;
  createdAt: number;
  snapshotAt: string;
  custom?: boolean;
}

const PLAN_TTL_MS = 60_000;
const READ_KEY = "eflux.agent-pulse.read";
const SEEN_KEY = "eflux.agent-pulse.seen";

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function roundedFive(value: number): number {
  return clamp(Math.round(value / 5) * 5, 0, 100);
}

function finiteNumber(value: string | number | null | undefined): number | null {
  if (value == null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function latestRiskBudget(performance?: ManagedVPPPerformance): number {
  return performance?.reflections.find((entry) => entry.ok && entry.risk_budget != null)?.risk_budget ?? 1;
}

function calculateComplexity(
  snapshot: MarketSnapshot | null,
  events: MarketEvent[],
  stale: boolean,
  agent: ManagedVPP,
  performance?: ManagedVPPPerformance,
): ComplexityAssessment {
  let total = 0;
  const weightedReasons: Array<[number, string]> = [];
  const add = (points: number, reason: string, visibleAt = 5) => {
    total += points;
    if (points >= visibleAt) weightedReasons.push([points, reason]);
  };

  const bid = finiteNumber(snapshot?.best_bid);
  const ask = finiteNumber(snapshot?.best_ask);
  if (bid != null && ask != null && ask >= bid && ask + bid > 0) {
    const spreadRatio = (ask - bid) / ((ask + bid) / 2);
    add(clamp(spreadRatio * 100, 0, 20), "Wide bid/ask spread");
  } else {
    add(8, "Thin or one-sided order book");
  }

  const ratio = snapshot?.balance.supply_demand_ratio;
  if (ratio == null) {
    add(10, "Supply/demand balance is unresolved");
  } else {
    add(clamp(Math.abs(ratio - 1) * 50, 0, 25), "Supply and demand are diverging");
  }

  const prices = events
    .filter((event) => event.kind === "tick")
    .slice(0, 30)
    .map((event) => finiteNumber(event.last_price))
    .filter((price): price is number => price != null && price > 0);
  if (prices.length >= 5) {
    const mean = prices.reduce((sum, price) => sum + price, 0) / prices.length;
    const variance = prices.reduce((sum, price) => sum + (price - mean) ** 2, 0) / prices.length;
    const coefficient = mean > 0 ? Math.sqrt(variance) / mean : 0;
    add(clamp(coefficient * 200, 0, 20), "Short-window price volatility is elevated");
  }

  const latestReflection = performance?.reflections[0];
  const modelPoints = agent.llm_health_state === "offline" ? 20 : agent.llm_health_state === "degraded" ? 10 : latestReflection && !latestReflection.ok ? 8 : 0;
  add(modelPoints, agent.llm_health_state === "offline" ? "LLM strategist is offline" : "LLM strategist confidence is degraded");

  const riskBudget = latestRiskBudget(performance);
  if (riskBudget < 0.6) add(clamp((0.6 - riskBudget) * 25, 0, 10), "Agent already reduced its risk budget", 3);
  if (riskBudget > 1.2) add(clamp((riskBudget - 1.2) * 20, 0, 8), "Agent is using an elevated risk budget", 3);

  if (stale) add(15, "Market data is stale");
  if (!snapshot) add(10, "No current market snapshot");

  return {
    score: clamp(Math.round(total), 0, 100),
    reasons: weightedReasons.sort((a, b) => b[0] - a[0]).slice(0, 3).map(([, reason]) => reason),
  };
}

function sameList(a?: string[] | null, b?: string[] | null): boolean {
  return JSON.stringify(a ?? []) === JSON.stringify(b ?? []);
}

function strategyChanges(current: ReflectionEntry, previous?: ReflectionEntry): string[] {
  if (!current.ok) return [];
  const changes: string[] = [];
  const pct = (value: number | null | undefined) => value == null ? "—" : `${Math.round(value * 100)}%`;
  if (!previous || current.risk_budget !== previous.risk_budget) {
    changes.push(`Risk budget ${previous ? pct(previous.risk_budget) : "—"} → ${pct(current.risk_budget)}`);
  }
  if (!previous || current.soc_target !== previous.soc_target) {
    changes.push(`SOC target ${previous ? pct(previous.soc_target) : "—"} → ${pct(current.soc_target)}`);
  }
  if (!previous || !sameList(current.preferred_modes, previous.preferred_modes)) {
    changes.push(`Preferred modes: ${current.preferred_modes?.join(", ") || "none"}`);
  }
  if (!previous || current.mode_pin !== previous.mode_pin) {
    if (current.mode_pin) changes.push(`Pinned mode: ${current.mode_pin}`);
  }
  return changes.slice(0, 3);
}

function buildTimeline(performance?: ManagedVPPPerformance): TimelineItem[] {
  if (!performance) return [];
  const trades: TimelineItem[] = performance.recent_trades.map((trade) => {
    const side = trade.side === "buy" ? "Bought" : "Sold";
    const counterparty = trade.counterparty ? ` with ${trade.counterparty}` : "";
    return {
      id: `trade-${trade.trade_id}`,
      ts: new Date(trade.wall_ts).getTime(),
      kind: "trade",
      label: `${side} ${Number(trade.qty).toFixed(3)} kWh`,
      summary: `$${Number(trade.price).toFixed(2)}/MWh${counterparty}`,
      detail: `Cash impact $${Math.abs(Number(trade.cash_usd)).toFixed(4)}`,
    };
  });
  const guidance: TimelineItem[] = performance.reflections.map((entry, index) => {
    const changes = strategyChanges(entry, performance.reflections[index + 1]);
    return {
      id: `guidance-${entry.ts}`,
      ts: new Date(entry.ts).getTime(),
      kind: entry.ok ? "guidance" : "error",
      label: entry.ok ? "Strategy guidance updated" : "Strategy review failed",
      summary: entry.ok ? (changes[0] ?? "Guidance refreshed with no material parameter change") : (entry.error ?? "The previous safe guidance remains active"),
      detail: entry.ok ? (entry.execution_style || entry.rationale) : undefined,
    };
  });
  return [...trades, ...guidance]
    .filter((item) => Number.isFinite(item.ts))
    .sort((a, b) => b.ts - a.ts)
    .slice(0, 12);
}

function makePlans(
  assessment: ComplexityAssessment,
  performance: ManagedVPPPerformance | undefined,
  marketMode: string,
  snapshotAt: string,
): StrategyPlan[] {
  const now = Date.now();
  const currentRisk = latestRiskBudget(performance);
  const cautiousMode = marketMode === "realprice" ? "wait_for_better" : "hold_energy";
  const passiveMode = marketMode === "realprice" ? "wait_for_better" : "passive_market_make";
  const plans: StrategyPlan[] = [];

  if (assessment.score >= 80) {
    plans.push({
      id: "pause",
      title: "Pause new orders",
      score: 95,
      risk: "low",
      summary: "Stand down until a fresh market review is available.",
      tradeoff: "Protects the current position, but may miss short-lived opportunities.",
      changes: ["Pin the next strategy window to noop", "Set risk budget to 0%", "Existing fills remain unchanged"],
      action: "guidance",
      payload: { mode_pin: "noop", risk_budget: 0, execution_style: "Owner paused new orders while market signals conflict.", lesson: "Manual Agent Pulse safety decision." },
      createdAt: now,
      snapshotAt,
    });
  }

  plans.push({
    id: "reduce-risk",
    title: "Reduce risk and observe",
    score: roundedFive(Math.min(85, 65 + assessment.score * 0.25)),
    risk: "low",
    summary: "Keep the agent active with smaller, less aggressive decisions.",
    tradeoff: "Lowers exposure while preserving optionality; execution may be slower.",
    changes: [`Risk budget ${Math.round(currentRisk * 100)}% → ${Math.round(Math.min(currentRisk, 0.45) * 100)}%`, `Prefer ${cautiousMode}`, "Avoid aggressive_taker"],
    action: "guidance",
    payload: {
      risk_budget: Math.min(currentRisk, 0.45),
      preferred_modes: [cautiousMode],
      avoid_modes: ["aggressive_taker"],
      soc_target: Math.max(performance?.soc_frac ?? 0.5, 0.55),
      execution_style: "Use smaller, patient orders while signals remain mixed.",
      lesson: "Manual Agent Pulse risk reduction.",
    },
    createdAt: now,
    snapshotAt,
  });

  plans.push({
    id: "patient-execution",
    title: marketMode === "realprice" ? "Wait for a better grid price" : "Use passive execution",
    score: roundedFive(80 - Math.abs(assessment.score - 55) * 0.25),
    risk: "medium",
    summary: "Continue participating without crossing the market aggressively.",
    tradeoff: "Can improve execution quality, but orders may not fill.",
    changes: ["Risk budget → 60%", `Prefer ${passiveMode}`, "Avoid aggressive_taker"],
    action: "guidance",
    payload: {
      risk_budget: 0.6,
      preferred_modes: [passiveMode],
      avoid_modes: ["aggressive_taker"],
      execution_style: "Prefer patient execution and wait for better pricing.",
      lesson: "Manual Agent Pulse execution choice.",
    },
    createdAt: now,
    snapshotAt,
  });

  plans.push({
    id: "platform",
    title: "Keep the platform strategy",
    score: roundedFive(85 - assessment.score * 0.5),
    risk: assessment.score >= 65 ? "high" : "medium",
    summary: "Return steering to the platform LLM and its current risk gate.",
    tradeoff: "Preserves the original strategy, including its present market exposure.",
    changes: ["Remove manual guidance", "Resume platform LLM steering", "Keep platform risk gates enabled"],
    action: "release",
    createdAt: now,
    snapshotAt,
  });

  return plans
    .slice(0, 4)
    .sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
}

function parseCustomPlan(
  input: string,
  assessment: ComplexityAssessment,
  performance: ManagedVPPPerformance | undefined,
  marketMode: string,
  snapshotAt: string,
): { plan?: StrategyPlan; error?: string } {
  const text = input.trim();
  if (text.length < 4) return { error: "Describe the desired direction in a little more detail." };
  const lower = text.toLowerCase();
  const matches = {
    pause: /pause|stop|halt|no new|暂停|停止|不要.*(?:下单|开仓|交易)|不再.*(?:下单|开仓)/i.test(lower),
    reduce: /conservative|cautious|reduce|lower.*risk|smaller|保守|降低.*风险|减少.*仓|减小.*仓|收紧/i.test(lower),
    wait: /wait|observe|passive|maker|观望|观察|被动|等待/i.test(lower),
    battery: /battery|\bsoc\b|reserve|电池|储能/i.test(lower),
    release: /resume|platform|original strategy|恢复.*平台|恢复.*策略|原.*策略/i.test(lower),
    aggressive: /aggressive|increase.*risk|press harder|more risk|积极|激进|增加.*风险|扩大.*仓/i.test(lower),
  };
  const matched = Object.entries(matches).filter(([, value]) => value).map(([key]) => key);
  if (matched.length === 0) {
    return { error: "I could not turn that into a bounded plan. Try “reduce risk and observe”, “pause new orders”, or “resume the platform strategy”." };
  }
  if (matches.release && matched.length > 1) return { error: "The prompt mixes platform control with a manual change. Choose one direction." };

  const now = Date.now();
  const auditLesson = `Owner prompt: ${text}`.slice(0, 200);
  const common = { createdAt: now, snapshotAt, custom: true };
  if (matches.release) {
    return { plan: { ...common, id: "custom-release", title: "Resume the platform strategy", score: null, risk: "medium", summary: "Remove manual guidance and hand steering back to the platform LLM.", tradeoff: "The current platform strategy becomes active again.", changes: ["Remove manual guidance", "Resume platform LLM steering"], action: "release" } };
  }
  if (matches.pause) {
    return { plan: { ...common, id: "custom-pause", title: "Pause new orders", score: null, risk: "low", summary: "Translate your prompt into a bounded no-new-orders instruction.", tradeoff: "The agent will not pursue new opportunities until control is restored.", changes: ["Pin mode to noop", "Risk budget → 0%"], action: "guidance", payload: { mode_pin: "noop", risk_budget: 0, execution_style: "Owner paused new orders from Agent Pulse.", lesson: auditLesson } } };
  }

  const currentRisk = latestRiskBudget(performance);
  let riskBudget = matches.reduce ? Math.min(currentRisk, 0.4) : matches.aggressive ? 0.9 : 0.6;
  if (matches.aggressive && assessment.score >= 65) riskBudget = 0.75;
  const preferredModes: string[] = [];
  if (matches.wait || matches.reduce) preferredModes.push(marketMode === "realprice" ? "wait_for_better" : "passive_market_make");
  if (matches.battery) preferredModes.push(marketMode === "realprice" ? "grid_charge_on_dip" : "battery_arbitrage");
  const changes = [`Risk budget → ${Math.round(riskBudget * 100)}%`];
  if (preferredModes.length) changes.push(`Prefer ${preferredModes.join(", ")}`);
  if (matches.reduce || matches.wait) changes.push("Avoid aggressive_taker");
  if (matches.battery) changes.push("SOC target → 65%");
  return {
    plan: {
      ...common,
      id: "custom-bounded",
      title: "Apply a bounded custom strategy",
      score: null,
      risk: matches.aggressive ? "high" : matches.reduce || matches.wait ? "low" : "medium",
      summary: "Your prompt has been converted into structured, risk-gated guidance.",
      tradeoff: matches.aggressive && assessment.score >= 65 ? "Market complexity is high, so the requested risk increase is capped at 75%." : "Only the previewed parameters will be submitted; the raw prompt is audit context only.",
      changes,
      action: "guidance",
      payload: {
        risk_budget: riskBudget,
        preferred_modes: preferredModes,
        avoid_modes: matches.reduce || matches.wait ? ["aggressive_taker"] : [],
        soc_target: matches.battery ? 0.65 : null,
        execution_style: matches.aggressive ? "Seek opportunities within the capped risk budget." : "Follow the owner-selected bounded execution posture.",
        lesson: auditLesson,
      },
    },
  };
}

function timeLabel(timestamp: number): string {
  return new Date(timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function riskClass(risk: StrategyPlan["risk"]): string {
  if (risk === "low") return "bg-[var(--success-soft)] text-[var(--success)]";
  if (risk === "high") return "bg-[var(--danger-soft)] text-[var(--danger)]";
  return "bg-[var(--warning-soft)] text-[var(--warning)]";
}

export default function AgentDock() {
  const { token } = useAuth();
  const location = useLocation();
  const { snapshot, recent, stale, state: streamState } = useMarket();
  const [agents, setAgents] = useState<ManagedVPP[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [performance, setPerformance] = useState<ManagedVPPPerformance>();
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<DockTab>("activity");
  const [selectedPlan, setSelectedPlan] = useState<StrategyPlan | null>(null);
  const [customInput, setCustomInput] = useState("");
  const [customError, setCustomError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [readAt, setReadAt] = useState(0);

  const loadAgents = useCallback(async () => {
    if (!token) return;
    try {
      const rows = await listManagedVPPs();
      setAgents(rows);
      setSelectedId((current) => current != null && rows.some((row) => row.id === current) ? current : (rows[0]?.id ?? null));
    } catch {
      // Keep the last successful roster; the app-wide connection banner owns transport errors.
    }
  }, [token]);

  const loadPerformance = useCallback(async (id: number) => {
    try {
      setPerformance(await fetchManagedVPPPerformance(id));
    } catch {
      // Preserve the last factual snapshot while the connection recovers.
    }
  }, []);

  useEffect(() => {
    if (!token) { setAgents([]); setSelectedId(null); return; }
    void loadAgents();
    const timer = window.setInterval(loadAgents, 30_000);
    return () => window.clearInterval(timer);
  }, [token, location.pathname, loadAgents]);

  useEffect(() => {
    if (selectedId == null) { setPerformance(undefined); return; }
    setSelectedPlan(null);
    setCustomInput("");
    setCustomError(null);
    setActionError(null);
    setResult(null);
    setPerformance(undefined);
    void loadPerformance(selectedId);
    const timer = window.setInterval(() => loadPerformance(selectedId), 4_000);
    return () => window.clearInterval(timer);
  }, [selectedId, loadPerformance]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open]);

  useEffect(() => {
    if (!selectedPlan) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [selectedPlan]);

  const agent = agents.find((row) => row.id === selectedId) ?? null;
  const assessment = useMemo(
    () => agent ? calculateComplexity(snapshot, recent, stale, agent, performance) : { score: 0, reasons: [] },
    [snapshot, recent, stale, agent, performance],
  );
  const timeline = useMemo(() => buildTimeline(performance), [performance]);
  const snapshotAt = snapshot?.sim_ts ?? new Date().toISOString();
  const marketMode = snapshot?.session.market_mode ?? "p2p";
  const plans = useMemo(
    () => makePlans(assessment, performance, marketMode, snapshotAt),
    [assessment, performance, marketMode, snapshotAt],
  );
  const hasClearRecommendation = Boolean(
    plans[0]?.score != null &&
    plans[0].score >= 70 &&
    (plans[1]?.score == null || plans[0].score - plans[1].score >= 10),
  );

  useEffect(() => {
    if (!agent) return;
    const stored = Number(localStorage.getItem(`${READ_KEY}.${agent.id}`) ?? 0);
    setReadAt(Number.isFinite(stored) ? stored : 0);
    const seenKey = `${SEEN_KEY}.${agent.id}`;
    if (!localStorage.getItem(seenKey)) {
      localStorage.setItem(seenKey, "1");
      setOpen(true);
      setTab("activity");
    }
  }, [agent?.id]);

  const newestTimestamp = timeline[0]?.ts ?? 0;
  const unread = timeline.filter((item) => item.ts > readAt).length;
  useEffect(() => {
    if (!open || !agent || newestTimestamp <= readAt) return;
    localStorage.setItem(`${READ_KEY}.${agent.id}`, String(newestTimestamp));
    setReadAt(newestTimestamp);
  }, [open, agent, newestTimestamp, readAt]);

  const attention = assessment.score >= 65;
  const offline = streamState !== "open" || stale || agent?.llm_health_state === "offline";
  const statusLabel = offline ? "Protected" : attention ? "Needs a choice" : "Watching";
  const latestSummary = attention
    ? `Market complexity ${assessment.score}/100 — review options`
    : timeline[0]?.label ?? "Agent is online and watching the market";
  const expiresIn = selectedPlan ? Math.max(0, Math.ceil((selectedPlan.createdAt + PLAN_TTL_MS - now) / 1_000)) : 0;
  const planExpired = selectedPlan != null && expiresIn === 0;

  const openDecision = () => {
    setOpen(true);
    setTab("decision");
    setSelectedPlan(null);
    setActionError(null);
    setResult(null);
  };

  const previewCustom = () => {
    if (!agent) return;
    const parsed = parseCustomPlan(customInput, assessment, performance, marketMode, snapshotAt);
    setCustomError(parsed.error ?? null);
    setSelectedPlan(parsed.plan ?? null);
    setActionError(null);
    setResult(null);
    setNow(Date.now());
  };

  const applyPlan = async () => {
    if (!agent || !selectedPlan || planExpired) return;
    setBusy(true); setActionError(null); setResult(null);
    try {
      if (selectedPlan.action === "release") await releaseGuidance(agent.id);
      else if (selectedPlan.payload) await putGuidance(agent.id, selectedPlan.payload);
      await Promise.all([loadAgents(), loadPerformance(agent.id)]);
      window.dispatchEvent(new CustomEvent("eflux:managed-vpp-updated", { detail: { id: agent.id } }));
      setResult(`${selectedPlan.title} is now active. The result was added to the guidance timeline.`);
      setSelectedPlan(null);
      setCustomInput("");
    } catch (error) {
      setActionError((error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!token || !agent) return null;

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-expanded="false"
        aria-label={`Open ${agent.name} Agent Pulse. ${latestSummary}`}
        className={`agent-pulse-capsule fixed bottom-4 right-4 z-50 flex max-w-[calc(100vw-2rem)] items-center gap-3 rounded-full border px-2.5 py-2 pr-4 text-left shadow-2xl backdrop-blur-xl transition hover:-translate-y-0.5 ${attention ? "border-[color-mix(in_srgb,var(--warning)_48%,transparent)]" : "border-[var(--border-strong)]"}`}
      >
        <span className={`agent-pulse-orb relative flex size-10 shrink-0 items-center justify-center rounded-full ${attention ? "agent-pulse-orb-attention" : ""}`}>
          <Bot size={19} aria-hidden="true" />
          <span className={`absolute -right-0.5 -top-0.5 size-2.5 rounded-full border-2 border-[var(--bg-elevated)] ${offline ? "bg-[var(--warning)]" : attention ? "bg-[var(--warning)]" : "bg-[var(--success)]"}`} />
        </span>
        <span className="min-w-0">
          <span className="flex items-center gap-2">
            <span className="max-w-40 truncate text-sm font-semibold text-[var(--text)]">{agent.name}</span>
            {unread > 0 && <span className="rounded-full bg-[var(--accent)] px-1.5 py-0.5 text-[10px] font-bold text-[var(--text-inverse)]">{Math.min(unread, 9)}{unread > 9 ? "+" : ""}</span>}
          </span>
          <span className="block max-w-64 truncate text-xs text-[var(--text-muted)]">{latestSummary}</span>
        </span>
      </button>
    );
  }

  return (
    <aside
      className="agent-pulse-panel fixed bottom-4 right-4 z-50 flex max-h-[calc(100vh-5.5rem)] w-[calc(100vw-2rem)] max-w-[440px] flex-col overflow-hidden rounded-2xl border border-[var(--border-strong)] shadow-2xl"
      role="dialog"
      aria-label={`${agent.name} Agent Pulse`}
    >
      <header className="agent-pulse-header shrink-0 border-b border-[var(--border)] px-4 pb-3 pt-4">
        <div className="flex items-start gap-3">
          <span className={`agent-pulse-orb relative flex size-11 shrink-0 items-center justify-center rounded-full ${attention ? "agent-pulse-orb-attention" : ""}`}><Bot size={21} /></span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h2 className="truncate text-base font-semibold text-[var(--text)]">{agent.name}</h2>
              <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${offline ? "bg-[var(--warning-soft)] text-[var(--warning)]" : attention ? "bg-[var(--warning-soft)] text-[var(--warning)]" : "bg-[var(--success-soft)] text-[var(--success)]"}`}>{statusLabel}</span>
            </div>
            <p className="mt-0.5 text-xs text-[var(--text-muted)]">Cloud agent · {agent.guidance_source === "external" ? "manual guidance" : "platform guidance"}</p>
          </div>
          <button type="button" onClick={() => setOpen(false)} aria-label="Minimize Agent Pulse" className="eflux-btn size-8 shrink-0"><X size={15} /></button>
        </div>

        {agents.length > 1 && (
          <label className="mt-3 block text-[11px] font-medium text-[var(--text-subtle)]">
            Active agent
            <select value={agent.id} onChange={(event) => { setSelectedId(Number(event.target.value)); setSelectedPlan(null); }} className="eflux-select mt-1 h-9 w-full px-3 text-xs">
              {agents.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}
            </select>
          </label>
        )}

        <div className="mt-3 rounded-xl border border-[var(--border)] bg-[color-mix(in_srgb,var(--surface-muted)_72%,transparent)] p-3">
          <div className="flex items-center justify-between gap-3 text-xs">
            <span className="flex items-center gap-1.5 font-semibold text-[var(--text)]"><Gauge size={14} className={attention ? "text-[var(--warning)]" : "text-[var(--accent)]"} /> Market complexity</span>
            <span className="font-mono font-semibold text-[var(--text)]">{assessment.score}/100</span>
          </div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-[var(--bg-muted)]">
            <div className={`h-full rounded-full transition-[width] duration-500 ${attention ? "bg-[var(--warning)]" : "bg-[var(--accent)]"}`} style={{ width: `${assessment.score}%` }} />
          </div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {(assessment.reasons.length ? assessment.reasons : ["Signals are within current guardrails"]).map((reason) => <span key={reason} className="rounded-full border border-[var(--border)] px-2 py-0.5 text-[10px] text-[var(--text-muted)]">{reason}</span>)}
          </div>
        </div>
      </header>

      <div className="grid shrink-0 grid-cols-2 border-b border-[var(--border)] bg-[var(--surface-inset)] p-1">
        <button type="button" onClick={() => setTab("activity")} className={`flex items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-xs font-semibold ${tab === "activity" ? "bg-[var(--surface-hover)] text-[var(--text)]" : "text-[var(--text-muted)]"}`}><Activity size={14} /> Activity</button>
        <button type="button" onClick={openDecision} className={`flex items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-xs font-semibold ${tab === "decision" ? "bg-[var(--surface-hover)] text-[var(--text)]" : attention ? "text-[var(--warning)]" : "text-[var(--text-muted)]"}`}><Sparkles size={14} /> Decision {attention && <span className="size-1.5 rounded-full bg-[var(--warning)]" />}</button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {tab === "activity" ? (
          <div className="space-y-4">
            <div className="flex items-start gap-2 rounded-xl bg-[var(--accent-soft)] p-3 text-xs text-[var(--text)]">
              <ShieldCheck size={16} className="mt-0.5 shrink-0 text-[var(--accent)]" />
              <p><span className="font-semibold">I’ll stay quiet during routine operation.</span> I’ll surface important trades, strategy changes, and choices that need your preference.</p>
            </div>

            <div className="flex items-center justify-between gap-2">
              <h3 className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--text-subtle)]">Recent facts & guidance</h3>
              <button type="button" onClick={() => loadPerformance(agent.id)} className="flex items-center gap-1 text-[11px] text-[var(--text-muted)] hover:text-[var(--text)]"><RefreshCw size={12} /> Refresh</button>
            </div>

            {timeline.length === 0 ? (
              <div className="rounded-xl border border-dashed border-[var(--border-strong)] px-4 py-7 text-center">
                <Bot size={22} className="mx-auto text-[var(--accent)]" />
                <p className="mt-2 text-sm font-medium text-[var(--text)]">Watching the market</p>
                <p className="mt-1 text-xs text-[var(--text-muted)]">Transactions and strategy changes will appear here.</p>
              </div>
            ) : (
              <ol className="space-y-2.5">
                {timeline.slice(0, 8).map((item) => (
                  <li key={item.id} className="rounded-xl border border-[var(--border)] bg-[var(--surface-muted)] p-3">
                    <div className="flex items-start gap-2.5">
                      <span className={`mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg ${item.kind === "trade" ? "bg-[var(--accent-soft)] text-[var(--accent)]" : item.kind === "error" ? "bg-[var(--danger-soft)] text-[var(--danger)]" : "bg-[var(--violet-soft)] text-[var(--violet)]"}`}>
                        {item.kind === "trade" ? <Activity size={13} /> : item.kind === "error" ? <AlertTriangle size={13} /> : <Sparkles size={13} />}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-start justify-between gap-2">
                          <p className="text-xs font-semibold text-[var(--text)]">{item.label}</p>
                          <time className="shrink-0 font-mono text-[10px] text-[var(--text-subtle)]">{timeLabel(item.ts)}</time>
                        </div>
                        <p className="mt-1 text-xs text-[var(--text-muted)]">{item.summary}</p>
                        {item.detail && <p className="mt-1 line-clamp-2 text-[11px] text-[var(--text-subtle)]">{item.detail}</p>}
                      </div>
                    </div>
                  </li>
                ))}
              </ol>
            )}

            {attention && <button type="button" onClick={openDecision} className="eflux-btn eflux-btn-primary h-10 w-full px-4 text-sm font-semibold">Review {plans.length} strategy options <ArrowRight size={15} /></button>}
          </div>
        ) : (
          <div className="space-y-4">
            {!agent.llm_enabled ? (
              <div className="rounded-xl border border-[var(--border)] bg-[var(--surface-muted)] p-4">
                <ShieldCheck size={20} className="text-[var(--accent)]" />
                <h3 className="mt-2 text-sm font-semibold text-[var(--text)]">This agent uses deterministic execution</h3>
                <p className="mt-1 text-xs text-[var(--text-muted)]">Manual strategy steering is available for LLM-enabled deployments. The platform risk gate remains active.</p>
              </div>
            ) : (
              <>
                <div className={`rounded-xl border p-3 ${attention ? "border-[color-mix(in_srgb,var(--warning)_38%,transparent)] bg-[var(--warning-soft)]" : "border-[var(--border)] bg-[var(--surface-muted)]"}`}>
                  <div className="flex items-start gap-2">
                    {attention ? <AlertTriangle size={16} className="mt-0.5 shrink-0 text-[var(--warning)]" /> : <ShieldCheck size={16} className="mt-0.5 shrink-0 text-[var(--success)]" />}
                    <div>
                      <p className="text-xs font-semibold text-[var(--text)]">{attention ? "Several paths are reasonable" : "No manual action is required"}</p>
                      <p className="mt-1 text-xs text-[var(--text-muted)]">{attention ? "Choose within 60 seconds of previewing a plan. If you do nothing, the current risk-gated strategy remains active." : "You can still preview bounded alternatives without changing the running strategy."}</p>
                    </div>
                  </div>
                </div>

                {result && <div className="flex items-start gap-2 rounded-xl bg-[var(--success-soft)] p-3 text-xs text-[var(--success)]"><CheckCircle2 size={16} className="mt-0.5 shrink-0" />{result}</div>}
                {actionError && <div className="flex items-start gap-2 rounded-xl bg-[var(--danger-soft)] p-3 text-xs text-[var(--danger)]"><AlertTriangle size={16} className="mt-0.5 shrink-0" />{actionError}</div>}

                <div>
                  <div className="mb-2 flex items-baseline justify-between gap-2">
                    <h3 className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--text-subtle)]">Agent options</h3>
                    <span className="text-[10px] text-[var(--text-subtle)]">{hasClearRecommendation ? "Scores are fit, not profit probability" : "No clear preference · fit scores only"}</span>
                  </div>
                  <div className="space-y-2">
                    {plans.map((plan, index) => (
                      <button key={plan.id} type="button" onClick={() => { setSelectedPlan({ ...plan, createdAt: Date.now(), snapshotAt }); setNow(Date.now()); setCustomError(null); setResult(null); }} className={`w-full rounded-xl border p-3 text-left transition hover:border-[var(--border-strong)] hover:bg-[var(--surface-hover)] ${selectedPlan?.id === plan.id ? "border-[var(--accent)] bg-[var(--accent-soft)]" : "border-[var(--border)] bg-[var(--surface-muted)]"}`}>
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="text-xs font-semibold text-[var(--text)]">{plan.title}</span>
                              {index === 0 && hasClearRecommendation && <span className="rounded-full bg-[var(--accent-soft)] px-2 py-0.5 text-[9px] font-bold uppercase tracking-wide text-[var(--accent)]">Agent pick</span>}
                            </div>
                            <p className="mt-1 text-[11px] text-[var(--text-muted)]">{plan.summary}</p>
                          </div>
                          <span className="shrink-0 font-mono text-sm font-semibold text-[var(--text)]">{plan.score}/100</span>
                        </div>
                        <div className="mt-2 flex items-center justify-between gap-2">
                          <span className={`rounded-full px-2 py-0.5 text-[9px] font-semibold uppercase ${riskClass(plan.risk)}`}>{plan.risk} risk</span>
                          <span className="text-[10px] text-[var(--text-subtle)]">Preview <ArrowRight size={10} className="inline" /></span>
                        </div>
                      </button>
                    ))}
                  </div>
                </div>

                <div className="rounded-xl border border-[var(--border)] bg-[var(--surface-muted)] p-3">
                  <label className="flex items-center gap-1.5 text-xs font-semibold text-[var(--text)]"><MessageSquareText size={14} className="text-[var(--violet)]" /> Other</label>
                  <p className="mt-1 text-[11px] text-[var(--text-muted)]">Describe a bounded direction. I’ll show structured changes before anything is sent.</p>
                  <textarea value={customInput} onChange={(event) => { setCustomInput(event.target.value); setCustomError(null); }} rows={3} maxLength={500} placeholder="e.g. Reduce risk and observe, but keep battery reserves above 65%." className="eflux-input mt-2 w-full resize-none px-3 py-2 text-xs outline-none" />
                  {customError && <p className="mt-1 text-[11px] text-[var(--danger)]">{customError}</p>}
                  <button type="button" onClick={previewCustom} className="eflux-btn mt-2 h-8 w-full px-3 text-xs">Create safe preview</button>
                </div>

                {selectedPlan && (
                  <div className="rounded-xl border border-[var(--accent)] bg-[var(--accent-soft)] p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--accent)]">Execution preview</p>
                        <h3 className="mt-1 text-sm font-semibold text-[var(--text)]">{selectedPlan.title}</h3>
                      </div>
                      <span className={`rounded-full px-2 py-0.5 text-[9px] font-semibold uppercase ${riskClass(selectedPlan.risk)}`}>{selectedPlan.risk} risk</span>
                    </div>
                    <ul className="mt-3 space-y-1.5">
                      {selectedPlan.changes.map((change) => <li key={change} className="flex items-start gap-2 text-xs text-[var(--text)]"><CheckCircle2 size={13} className="mt-0.5 shrink-0 text-[var(--accent)]" />{change}</li>)}
                    </ul>
                    <p className="mt-3 text-[11px] text-[var(--text-muted)]">{selectedPlan.tradeoff}</p>
                    <div className={`mt-3 flex items-center gap-1.5 text-[10px] ${planExpired ? "text-[var(--danger)]" : "text-[var(--text-subtle)]"}`}><Clock3 size={12} />{planExpired ? "Preview expired — select the plan again" : `Valid for ${expiresIn}s · snapshot ${new Date(selectedPlan.snapshotAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`}</div>
                    <button type="button" onClick={applyPlan} disabled={busy || planExpired} className="eflux-btn eflux-btn-primary mt-3 h-9 w-full px-4 text-xs font-semibold disabled:opacity-50">{busy ? "Validating…" : "Confirm and apply"}</button>
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </div>

      <footer className="flex shrink-0 items-center justify-between gap-3 border-t border-[var(--border)] bg-[var(--surface-inset)] px-4 py-3">
        <span className="text-[10px] text-[var(--text-subtle)]">Facts, guidance and user decisions are kept separate.</span>
        <Link to={`/vpps/${agent.id}`} onClick={() => setOpen(false)} className="flex shrink-0 items-center gap-1 text-xs font-semibold text-[var(--accent)] hover:underline">Open cockpit <ExternalLink size={12} /></Link>
      </footer>
    </aside>
  );
}
