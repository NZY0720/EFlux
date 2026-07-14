import { useEffect, useState, type FormEvent } from "react";
import { BatteryCharging, BrainCircuit, Copy, KeyRound, ListChecks, MessagesSquare, Save, Settings2, Terminal, Trash2 } from "lucide-react";
import { type ApiKeyInfo, listApiKeys, mintApiKey, releaseGuidance, revokeApiKey, sayInChatroom, setChatPrefs, updateManagedVPP } from "../../api/client";
import type { AlgorithmParam, ManagedVPP, ManagedVPPPerformance, ReflectionEntry, VPP } from "../../api/types";
import { CardTitle, DashboardCard, EmptyState, StatusPill, TableShell } from "../../components/DashboardCard";
import { useMarket } from "../../state/marketStream";

type AlgorithmParamValue = number | string | boolean;

const CHAT_COLORS = ["#059669", "#0284c7", "#7c3aed", "#d97706", "#e11d48", "#0d9488", "#9333ea", "#0891b2"];

export function ManagedControls({
  vpp,
  models,
  onSaved,
  onDelete,
  onError,
}: {
  vpp: ManagedVPP;
  models: string[];
  onSaved: () => Promise<void> | void;
  onDelete: () => void;
  onError: (msg: string | null) => void;
}) {
  const hybrid = isLlmManaged(vpp);
  const [persona, setPersona] = useState(vpp.persona ?? "");
  const [model, setModel] = useState(vpp.model ?? "");
  const [demandBeta, setDemandBeta] = useState(0.5);
  const [betaDirty, setBetaDirty] = useState(false);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  // Chatroom presence: speak as the agent + voice/color/avatar prefs.
  const [chatOpen, setChatOpen] = useState(false);
  const [sayText, setSayText] = useState("");
  const [said, setSaid] = useState<string | null>(null);
  const [chatStyle, setChatStyle] = useState(vpp.chat_style ?? "");
  const [chatColor, setChatColor] = useState(vpp.chat_color ?? "");
  const [chatAvatar, setChatAvatar] = useState(vpp.chat_avatar ?? "");

  const onSave = async () => {
    setBusy(true);
    onError(null);
    try {
      await updateManagedVPP(vpp.id, {
        persona: persona.trim(),
        ...(model ? { model } : {}),
        // Only send agent_params if the slider was touched, so a persona-only edit
        // doesn't silently reset demand_beta to the form default.
        ...(betaDirty ? { agent_params: { demand_beta: demandBeta } } : {}),
      });
      await onSaved();
      setOpen(false);
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const onReleaseGuidance = async () => {
    setBusy(true);
    onError(null);
    try {
      await releaseGuidance(vpp.id);
      await onSaved();
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const onSay = async (e: FormEvent) => {
    e.preventDefault();
    if (!sayText.trim()) return;
    setBusy(true);
    onError(null);
    try {
      await sayInChatroom(vpp.id, sayText.trim());
      setSaid(sayText.trim());
      setSayText("");
    } catch (err) {
      onError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const onSaveChatPrefs = async () => {
    setBusy(true);
    onError(null);
    try {
      await setChatPrefs(vpp.id, {
        style: chatStyle.trim() || null,
        color: chatColor || null,
        avatar: chatAvatar.trim() || null,
      });
      await onSaved();
    } catch (err) {
      onError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-2 border-t border-[var(--border)] px-3 py-2">
      {hybrid && (
        <button type="button" onClick={() => setOpen((o) => !o)} className="eflux-btn h-8 px-3 text-xs">
          <Settings2 size={14} />
          {open ? "Close" : "Tune preferences"}
        </button>
      )}
      {hybrid && vpp.guidance_source === "external" && (
        <button
          type="button"
          onClick={onReleaseGuidance}
          disabled={busy}
          className="eflux-btn h-8 px-3 text-xs disabled:opacity-50"
          title="Your model is steering this agent (Tier A3). Hand strategy back to the platform LLM."
        >
          <BrainCircuit size={14} />
          Return to platform LLM
        </button>
      )}
      <button type="button" onClick={() => setChatOpen((o) => !o)} className="eflux-btn h-8 px-3 text-xs">
        <MessagesSquare size={14} />
        {chatOpen ? "Close chatroom" : "Chatroom"}
      </button>
      <button type="button" onClick={onDelete} className="eflux-btn eflux-btn-danger h-8 px-3 text-xs">
        <Trash2 size={14} />
        Delete
      </button>
      {chatOpen && (
        <div className="mt-2 w-full space-y-3 rounded-lg border border-[var(--border)] bg-[var(--surface-muted)] p-3">
          <form onSubmit={onSay} className="space-y-1.5">
            <label className="block text-xs font-medium text-[var(--text-muted)]" htmlFor={`say-${vpp.id}`}>
              Speak as {vpp.name} (posts publicly; the other agents can reply)
            </label>
            <div className="flex gap-2">
              <input
                id={`say-${vpp.id}`}
                value={sayText}
                onChange={(e) => setSayText(e.target.value)}
                maxLength={200}
                placeholder="say something in the room"
                className="eflux-input min-w-0 flex-1 rounded-md px-3 py-1.5 text-sm outline-none"
              />
              <button
                type="submit"
                disabled={busy || !sayText.trim()}
                className="eflux-btn eflux-btn-primary h-8 px-3 text-xs disabled:opacity-50"
              >
                Say it
              </button>
            </div>
            {said && (
              <p className="text-[11px] text-[var(--success)]">Posted: "{said}"</p>
            )}
          </form>
          <label className="block text-xs font-medium text-[var(--text-muted)]">
            Chat voice (tone only; does not touch trading)
            <input
              value={chatStyle}
              onChange={(e) => setChatStyle(e.target.value)}
              maxLength={200}
              placeholder="e.g. dry one-liners, always quotes battery SOC"
              className="eflux-input mt-1 w-full rounded-md px-3 py-1.5 text-sm outline-none"
            />
          </label>
          <div className="flex flex-wrap items-end gap-4">
            <div>
              <div className="text-xs font-medium text-[var(--text-muted)]">Name color</div>
              <div className="mt-1.5 flex items-center gap-1.5">
                {CHAT_COLORS.map((c) => (
                  <button
                    key={c}
                    type="button"
                    onClick={() => setChatColor(chatColor === c ? "" : c)}
                    aria-label={`use color ${c}`}
                    className={`h-6 w-6 rounded-full border-2 transition-transform hover:scale-110 ${
                      chatColor === c ? "border-[var(--text)]" : "border-transparent"
                    }`}
                    style={{ backgroundColor: c }}
                  />
                ))}
              </div>
            </div>
            <label className="block text-xs font-medium text-[var(--text-muted)]">
              Avatar (one emoji)
              <input
                value={chatAvatar}
                onChange={(e) => setChatAvatar(e.target.value)}
                maxLength={4}
                placeholder="none"
                className="eflux-input mt-1 w-20 rounded-md px-3 py-1.5 text-center text-sm outline-none"
              />
            </label>
            <button
              type="button"
              disabled={busy}
              onClick={onSaveChatPrefs}
              className="eflux-btn h-8 px-3 text-xs disabled:opacity-50"
            >
              <Save size={14} />
              Save presence
            </button>
          </div>
        </div>
      )}
      {hybrid && open && (
        <div className="mt-2 w-full space-y-3 rounded-lg border border-[var(--border)] bg-[var(--surface-muted)] p-3">
          <p className="text-[11px] text-[var(--text-subtle)]">
            Applying changes restarts the agent&apos;s trading session (open orders reset; PnL is kept).
          </p>
          <label className="block text-xs font-medium text-[var(--text-muted)]">
            Strategy brief (persona)
            <textarea
              value={persona}
              onChange={(e) => setPersona(e.target.value)}
              rows={3}
              maxLength={600}
              className="eflux-input mt-1 w-full resize-none rounded-md px-3 py-2 text-sm outline-none"
            />
          </label>
          <SelectField label="LLM model" value={model} options={models} onChange={setModel} />
          <SliderField
            label={`Demand response (demand_beta ${demandBeta.toFixed(2)})`}
            value={demandBeta}
            min={0}
            max={1}
            step={0.05}
            onChange={(v) => {
              setDemandBeta(v);
              setBetaDirty(true);
            }}
          />
          <button
            type="button"
            disabled={busy}
            onClick={onSave}
            className="eflux-btn eflux-btn-primary h-8 px-3 text-xs disabled:opacity-50"
          >
            <Save size={14} />
            {busy ? "Saving..." : "Save & restart agent"}
          </button>
        </div>
      )}
    </div>
  );
}

export function ApiAutomationCard({ vpps, onError }: { vpps: VPP[]; onError: (msg: string | null) => void }) {
  const [keys, setKeys] = useState<ApiKeyInfo[]>([]);
  const [keyName, setKeyName] = useState("");
  const [minted, setMinted] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [selectedVppId, setSelectedVppId] = useState<number | null>(null);

  const reloadKeys = async () => {
    try {
      setKeys(await listApiKeys());
    } catch (e) {
      onError((e as Error).message);
    }
  };
  useEffect(() => {
    reloadKeys();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onMint = async (e: FormEvent) => {
    e.preventDefault();
    if (!keyName.trim()) return;
    setBusy(true);
    onError(null);
    try {
      const k = await mintApiKey(keyName.trim());
      setMinted(k.key);
      setKeyName("");
      await reloadKeys();
    } catch (err) {
      onError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const onRevoke = async (prefix: string) => {
    if (!window.confirm("Revoke this API key? Apps using it will stop working.")) return;
    onError(null);
    try {
      await revokeApiKey(prefix);
      await reloadKeys();
    } catch (err) {
      onError((err as Error).message);
    }
  };

  const activeKeys = keys.filter((k) => !k.revoked_at);
  // The VPP this snippet targets — the one the user picked, else their first VPP.
  const targetVpp = vpps.find((v) => v.id === selectedVppId) ?? vpps[0] ?? null;
  const targetId = targetVpp ? String(targetVpp.id) : "<your_vpp_id>";
  const snippet = `# pip install -e .   (then adapt the repo's examples/market_maker.py)
from eflux.sdk import EFluxClient

client = EFluxClient(base_url="http://localhost:8000", api_key="<YOUR_API_KEY>")

# Read the market, then submit a replay-safe batch for ${targetVpp ? `"${targetVpp.name}"` : "your VPP"}.
products = await client.products()
product = next(p for p in products if p["is_open"])
await client.submit_batch(
    orders=[{
        "vpp_id": ${targetId}, "side": "buy", "price": 48.0, "qty_kwh": 0.2,
        "product_id": product["product_id"], "purpose": "balance",
        "time_in_force": "good_til_gate", "ttl_sec": 120,
    }],
    idempotency_key="quote-001",  # a resend returns the original result, never a double order
)`;

  const copySnippet = async () => {
    try {
      await navigator.clipboard.writeText(snippet);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable — the user can select the text manually */
    }
  };

  return (
    <DashboardCard className="lg:col-span-2">
      <CardTitle icon={Terminal}>Automate your VPPs (external app)</CardTitle>
      <p className="mb-3 text-sm text-[var(--text-muted)]">
        First create a VPP under <span className="font-semibold text-[var(--text)]">My VPPs</span> above,
        then pick it here to drive it from your own local bot via the Agent Protocol. The platform
        provides the interface and policies; your decision logic stays on your machine.
      </p>

      <div className="space-y-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-[var(--text)]">
          <KeyRound size={15} className="text-[var(--accent)]" /> API keys
        </h3>
        <form onSubmit={onMint} className="flex flex-wrap gap-2">
          <input
            value={keyName}
            onChange={(e) => setKeyName(e.target.value)}
            placeholder="key name (e.g. market-maker-bot)"
            maxLength={100}
            className="eflux-input min-w-0 flex-1 rounded-md px-3 py-2 text-sm outline-none"
          />
          <button
            disabled={busy || !keyName.trim()}
            className="eflux-btn eflux-btn-primary h-9 px-4 text-sm font-semibold disabled:opacity-50"
          >
            <KeyRound size={14} /> Mint key
          </button>
        </form>
        {minted && (
          <div className="rounded-lg border border-[color-mix(in_srgb,var(--warning)_45%,transparent)] bg-[var(--warning-soft)] p-3 text-xs">
            <div className="mb-1 font-semibold text-[var(--warning)]">
              Copy this key now — it is shown only once.
            </div>
            <code className="block break-all font-mono text-[var(--text)]">{minted}</code>
          </div>
        )}
        {activeKeys.length > 0 ? (
          <ul className="space-y-1">
            {activeKeys.map((k) => (
              <li
                key={k.prefix}
                className="flex items-center justify-between gap-2 rounded-md bg-[var(--surface-inset)] px-3 py-1.5 text-xs"
              >
                <span className="min-w-0 truncate">
                  <span className="font-medium text-[var(--text)]">{k.name}</span>
                  <span className="ml-2 font-mono text-[var(--text-subtle)]">{k.prefix}…</span>
                </span>
                <button
                  type="button"
                  onClick={() => onRevoke(k.prefix)}
                  className="eflux-btn eflux-btn-danger h-7 px-2 text-xs"
                >
                  <Trash2 size={12} /> Revoke
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-[var(--text-subtle)]">No active keys. Mint one to authenticate your bot.</p>
        )}
      </div>

      <div className="mt-4 space-y-1 text-xs text-[var(--text-muted)]">
        <div>
          <span className="font-semibold text-[var(--text)]">Endpoint</span> —{" "}
          <code className="font-mono">POST /orders/batch</code> (Agent Protocol v1:{" "}
          <code className="font-mono">idempotency_key</code>, <code className="font-mono">deadline</code>, cancels-first)
        </div>
        <div>
          <span className="font-semibold text-[var(--text)]">Auth</span> —{" "}
          <code className="font-mono">Authorization: Bearer &lt;API_KEY&gt;</code>
        </div>
        <div>
          <span className="font-semibold text-[var(--text)]">Rate limit</span> — 120 burst / 2·s⁻¹ per account
          (429 on exceed); every order still passes the Trading Gateway V1.
        </div>
      </div>

      {vpps.length === 0 ? (
        <div className="mt-4 rounded-lg border border-dashed border-[var(--border)] bg-[var(--surface-inset)] p-3 text-xs text-[var(--text-muted)]">
          <span className="font-semibold text-[var(--text)]">No VPP to automate yet.</span> Create one
          under <span className="font-semibold">My VPPs</span> above, then pick it here to get a
          ready-to-run snippet that targets it.
        </div>
      ) : (
        <div className="mt-4 space-y-2">
          <SelectField
            label="Automate this VPP"
            value={targetId}
            options={vpps.map((v) => ({ value: String(v.id), label: `${v.name} (#${v.id})` }))}
            onChange={(val) => setSelectedVppId(Number(val))}
          />
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-[var(--text)]">Quick start</span>
            <button type="button" onClick={copySnippet} className="eflux-btn h-7 px-2 text-xs">
              <Copy size={12} /> {copied ? "Copied" : "Copy"}
            </button>
          </div>
          <pre className="eflux-inset overflow-x-auto rounded-lg p-3 text-[11px] leading-relaxed text-[var(--text)]">
            <code>{snippet}</code>
          </pre>
          <p className="text-[11px] text-[var(--text-subtle)]">
            Full runnable example: <code className="font-mono">examples/market_maker.py</code> · SDK:{" "}
            <code className="font-mono">eflux.sdk.EFluxClient</code> · MCP server:{" "}
            <code className="font-mono">eflux.mcp.server</code>.
          </p>
        </div>
      )}
    </DashboardCard>
  );
}

export function isLlmManaged(vpp: ManagedVPP): boolean {
  return vpp.llm_enabled;
}

const ALGORITHM_LABELS: Record<string, string> = {
  ppo: "PPO",
  truthful: "Truthful",
  zip: "ZIP",
  gd: "GD",
  aa: "AA",
};

export function algorithmChipLabel(vpp: ManagedVPP): string {
  const base = ALGORITHM_LABELS[vpp.algorithm] ?? vpp.algorithm ?? "managed";
  return isLlmManaged(vpp) ? `LLM + ${base}` : base;
}

export function SelectField({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: Array<string | { value: string; label: string }>;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block text-xs font-medium text-[var(--text-muted)]">
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="eflux-select mt-1 w-full rounded-md px-3 py-2 text-sm outline-none"
      >
        {options.map((o) => (
          <option key={typeof o === "string" ? o : o.value} value={typeof o === "string" ? o : o.value}>
            {typeof o === "string" ? o : o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export function AlgorithmParamField({
  param,
  value,
  onChange,
}: {
  param: AlgorithmParam;
  value: AlgorithmParamValue | undefined;
  onChange: (value: AlgorithmParamValue) => void;
}) {
  const inputValue = value ?? param.default ?? (isNumericParam(param) ? 0 : "");
  if (isNumericParam(param)) {
    const numericValue = typeof inputValue === "number" ? inputValue : Number(inputValue);
    return (
      <NumberField
        label={param.help ? `${param.name} - ${param.help}` : param.name}
        value={Number.isFinite(numericValue) ? numericValue : 0}
        step={param.type === "int" || param.type === "integer" ? "1" : "0.01"}
        min={param.min ?? undefined}
        max={param.max ?? undefined}
        onChange={onChange}
      />
    );
  }
  if (param.type === "bool" || param.type === "boolean") {
    return (
      <label className="flex items-center gap-2 text-xs font-medium text-[var(--text-muted)]">
        <input
          type="checkbox"
          checked={Boolean(inputValue)}
          onChange={(e) => onChange(e.target.checked)}
          className="h-4 w-4 accent-[var(--accent)]"
        />
        {param.help ? `${param.name} - ${param.help}` : param.name}
      </label>
    );
  }
  return (
    <label className="block text-xs font-medium text-[var(--text-muted)]">
      {param.help ? `${param.name} - ${param.help}` : param.name}
      <input
        value={String(inputValue)}
        onChange={(e) => onChange(e.target.value)}
        className="eflux-input mt-1 w-full rounded-md px-3 py-2 text-sm outline-none"
      />
    </label>
  );
}

function isNumericParam(param: AlgorithmParam): boolean {
  return ["float", "number", "int", "integer"].includes(param.type);
}

export function SliderField({
  label,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="block text-xs font-medium text-[var(--text-muted)]">
      {label}
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="mt-1 w-full accent-[var(--accent)]"
      />
    </label>
  );
}

export function NumberField({
  label,
  value,
  step,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  step: string;
  min?: number;
  max?: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="block text-xs font-medium text-[var(--text-muted)]">
      {label}
      <input
        type="number"
        step={step}
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="eflux-input mt-1 w-full rounded-md px-3 py-2 text-sm outline-none"
      />
    </label>
  );
}

export function TextNumberField({
  label,
  value,
  step,
  placeholder,
  onChange,
}: {
  label: string;
  value: string;
  step: string;
  placeholder: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block text-xs font-medium text-[var(--text-muted)]">
      {label}
      <input
        type="number"
        step={step}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="eflux-input mt-1 w-full rounded-md px-3 py-2 text-sm outline-none"
      />
    </label>
  );
}

export function LLMBadge({ state }: { state: string }) {
  const tone = state === "live" ? "success" : state === "degraded" ? "amber" : "muted";
  const labels: Record<string, string> = {
    live: "LLM live",
    degraded: "LLM degraded",
    offline: "LLM offline",
  };
  return <StatusPill tone={tone}>{labels[state] ?? state}</StatusPill>;
}

export function ManagedPerformancePanel({ data }: { data?: ManagedVPPPerformance }) {
  const { nameOf } = useMarket();
  const pnl = Number(data?.pnl ?? 0);
  const pnlClass = pnl >= 0 ? "text-[var(--success)]" : "text-[var(--danger)]";

  return (
    <div className="border-t border-[var(--border)] px-3 py-3">
      {!data ? (
        <EmptyState icon={BrainCircuit} title="Loading performance..." className="min-h-28" />
      ) : (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            <Metric label="PnL ($)" value={Number(data.pnl).toFixed(4)} valueClass={pnlClass} />
            <Metric label="Battery SOC" value={`${(data.soc_frac * 100).toFixed(1)}%`} icon={BatteryCharging} />
            <Metric label="Bought (kWh)" value={data.cumulative_energy_bought_kwh.toFixed(4)} />
            <Metric label="Sold (kWh)" value={data.cumulative_energy_sold_kwh.toFixed(4)} />
          </div>
          <ReflectionTimeline data={data} />
          <TableShell className="max-h-72">
            <table className="eflux-table min-w-[720px] text-xs">
              <thead className="sticky top-0 z-10">
                <tr>
                  <th className="px-2 py-2 text-left font-semibold">Time</th>
                  <th className="px-2 py-2 text-left font-semibold">Side</th>
                  <th className="px-2 py-2 text-right font-semibold">Price ($/MWh)</th>
                  <th className="px-2 py-2 text-right font-semibold">Qty (kWh)</th>
                  <th className="px-2 py-2 text-right font-semibold">Cash ($)</th>
                  <th className="px-2 py-2 text-right font-semibold">Counterparty</th>
                </tr>
              </thead>
              <tbody>
                {data.recent_trades.map((t) => (
                  <tr key={`${t.trade_id}-${t.side}`}>
                    <td className="px-2 py-1.5 text-[var(--text-muted)] tabular-nums">
                      {new Date(t.wall_ts).toLocaleTimeString("en-GB", { hour12: false })}
                    </td>
                    <td className={t.side === "buy" ? "px-2 py-1.5 text-[var(--success)]" : "px-2 py-1.5 text-[var(--danger)]"}>
                      {t.side}
                    </td>
                    <td className="px-2 py-1.5 text-right text-[var(--text)] tabular-nums">{Number(t.price).toFixed(2)}</td>
                    <td className="px-2 py-1.5 text-right text-[var(--text)] tabular-nums">{Number(t.qty).toFixed(4)}</td>
                    <td className="px-2 py-1.5 text-right text-[var(--text)] tabular-nums">{Number(t.cash_usd).toFixed(4)}</td>
                    <td className="px-2 py-1.5 text-right text-[var(--text-muted)]">
                      {t.counterparty ?? nameOf(t.counterparty_vpp_id)}
                    </td>
                  </tr>
                ))}
                {data.recent_trades.length === 0 && (
                  <tr>
                    <td colSpan={6} className="p-3">
                      <EmptyState icon={ListChecks} title="No trades yet" className="min-h-24" />
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </TableShell>
        </div>
      )}
    </div>
  );
}

function Metric({
  label,
  value,
  valueClass = "text-[var(--text)]",
  icon: Icon,
}: {
  label: string;
  value: string;
  valueClass?: string;
  icon?: typeof BatteryCharging;
}) {
  return (
    <div className="eflux-inset rounded-md px-2 py-2">
      <div className="flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wide text-[var(--text-subtle)]">
        {Icon && <Icon size={12} />}
        {label}
      </div>
      <div className={`mt-1 text-sm font-semibold tabular-nums ${valueClass}`}>{value}</div>
    </div>
  );
}

function guidanceSummary(r: ReflectionEntry): string {
  if (r.risk_budget !== null && r.risk_budget !== undefined) {
    const parts = [`risk ${(r.risk_budget * 100).toFixed(0)}%`];
    if (r.soc_target !== null && r.soc_target !== undefined) {
      parts.push(`SOC ${(r.soc_target * 100).toFixed(0)}%`);
    }
    if (r.preferred_modes?.length) parts.push(`prefer ${r.preferred_modes.slice(0, 2).join(", ")}`);
    return parts.join(" / ");
  }
  if (r.price_adjust !== null && r.price_adjust !== undefined && r.qty_scale !== null && r.qty_scale !== undefined) {
    return `price ${r.price_adjust >= 0 ? "+" : ""}${(r.price_adjust * 100).toFixed(1)}% / qty x${r.qty_scale.toFixed(2)}`;
  }
  return "guidance updated";
}

function guidanceText(r: ReflectionEntry): string {
  return r.execution_style || r.rationale || "(no rationale)";
}

function ReflectionTimeline({ data }: { data: ManagedVPPPerformance }) {
  const { reflections, llm_health: health } = data;
  if (health === null && reflections.length === 0) return null;

  return (
    <div>
      <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
        <h4 className="text-[11px] font-semibold uppercase tracking-wide text-[var(--text-subtle)]">Guidance timeline</h4>
        {health && (
          <span className="text-[11px] text-[var(--text-subtle)]">
            {health.ok_count} ok / {health.fail_count} failed
            {health.last_ok_ts &&
              ` / last ok ${new Date(health.last_ok_ts).toLocaleTimeString("en-GB", { hour12: false })}`}
          </span>
        )}
      </div>
      <div className="eflux-inset max-h-56 space-y-1.5 overflow-auto rounded-lg p-2">
        {reflections.length === 0 && (
          <p className="px-1 py-2 text-center text-xs text-[var(--text-subtle)]">
            No guidance yet - the agent consults the LLM every ~minute.
          </p>
        )}
        {reflections.map((r) => (
          <div key={r.ts} className="rounded-md border border-[var(--border)] bg-[var(--surface-muted)] px-2 py-1.5">
            <div className="flex flex-wrap items-center gap-2 text-[11px]">
              <span className="text-[var(--text-muted)] tabular-nums">
                {new Date(r.ts).toLocaleTimeString("en-GB", { hour12: false })}
              </span>
              {r.ok ? (
                <>
                  <StatusPill tone="success" className="py-0 text-[11px]">ok</StatusPill>
                  <span className="text-[var(--accent)] tabular-nums">{guidanceSummary(r)}</span>
                </>
              ) : (
                <StatusPill tone="danger" className="py-0 text-[11px]">failed</StatusPill>
              )}
            </div>
            <p className="mt-0.5 text-xs text-[var(--text)]">{r.ok ? guidanceText(r) : r.error}</p>
            {r.ok && r.lesson && (
              <p className="mt-0.5 text-[11px] italic text-[var(--text-muted)]">💡 {r.lesson}</p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
