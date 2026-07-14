import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  Bot,
  ChevronRight,
  PackageCheck,
  Plus,
  Search,
  ShieldCheck,
} from "lucide-react";
import { Link, useNavigate } from "react-router-dom";

import {
  createAgentRelease,
  fetchPlatformRuntimeIdentity,
  listAgentReleases,
  type AgentRelease,
  type CreateAgentReleaseInput,
  type Market,
  type Visibility,
} from "../api/ecosystem";
import {
  CardTitle,
  DashboardCard,
  EmptyState,
  StatusPill,
} from "../components/DashboardCard";
import { AgentsNav } from "../components/WorkspaceNav";

type MarketFilter = "all" | Market;

const marketTone = (market: Market) =>
  market === "realprice" ? "amber" : market === "p2p" ? "accent" : "violet";
const statusTone = (status: AgentRelease["status"]) =>
  status === "verified"
    ? "violet"
    : status === "published"
      ? "success"
      : "amber";
const humanize = (value: string) =>
  value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
const splitList = (value: string) =>
  value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
const recordText = (
  record: Record<string, unknown>,
  key: string,
  fallback: string,
) => (typeof record[key] === "string" ? (record[key] as string) : fallback);
const parseObject = (text: string, label: string): Record<string, unknown> => {
  const value = JSON.parse(text) as unknown;
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error(`${label} must be a JSON object.`);
  return value as Record<string, unknown>;
};

export default function AgentReleases() {
  const navigate = useNavigate();
  const [releases, setReleases] = useState<AgentRelease[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [market, setMarket] = useState<MarketFilter>("all");
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [version, setVersion] = useState("1");
  const [description, setDescription] = useState("");
  const [releaseMarket, setReleaseMarket] = useState<Market>("realprice");
  const [visibility, setVisibility] = useState<Visibility>("private");
  const [badges, setBadges] = useState("");
  const [recipeJson, setRecipeJson] = useState(
    JSON.stringify(
      {
        algorithm: "scripted",
        protocol_version: "1",
        observation_schema_version: "1",
        action_schema_version: "1",
        online_learning: false,
        fallback_strategy: "safe_hold",
        risk_limits: {
          max_open_orders: 20,
          max_new_orders_per_decision: 5,
          credit_limit_usd: 10000,
        },
        order_routing: {
          markets: ["realprice", "p2p", "hybrid"],
          default_route: "auto",
        },
      },
      null,
      2,
    ),
  );
  const [stateJson, setStateJson] = useState("{}");
  const [compatibilityJson, setCompatibilityJson] = useState(
    JSON.stringify(
      { portability: "reproducible", vpp_types: ["battery"] },
      null,
      2,
    ),
  );
  const [environmentJson, setEnvironmentJson] = useState(
    JSON.stringify(
      {
        runtime: "eflux-managed",
        dependencies_locked: true,
        agent_protocol_version: 1,
      },
      null,
      2,
    ),
  );

  const load = async () => {
    try {
      const [rows, runtime] = await Promise.all([
        listAgentReleases(),
        fetchPlatformRuntimeIdentity().catch(() => null),
      ]);
      setReleases(rows);
      if (runtime?.git_commit) {
        setEnvironmentJson((current) => {
          const environment = parseObject(current, "Environment");
          if (environment.git_commit || environment.container_image_digest)
            return current;
          return JSON.stringify(
            { ...environment, git_commit: runtime.git_commit },
            null,
            2,
          );
        });
      }
      setError(null);
    } catch (err) {
      setError((err as Error).message || "Unable to load agent releases.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return releases.filter(
      (release) =>
        (market === "all" || release.market === market) &&
        (!needle ||
          [
            release.name,
            release.description ?? "",
            release.version,
            recordText(release.recipe, "algorithm", ""),
          ].some((value) => value.toLowerCase().includes(needle))),
    );
  }, [market, query, releases]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setCreating(true);
    setError(null);
    try {
      const body: CreateAgentReleaseInput = {
        name: name.trim(),
        version: version.trim(),
        description: description.trim(),
        market: releaseMarket,
        visibility,
        badges: splitList(badges),
        recipe: parseObject(recipeJson, "Recipe"),
        state: parseObject(stateJson, "State"),
        compatibility: parseObject(compatibilityJson, "Compatibility"),
        environment: parseObject(environmentJson, "Environment"),
      };
      const created = await createAgentRelease(body);
      navigate(`/agents/releases/${created.id}`);
    } catch (err) {
      setError((err as Error).message || "Unable to create this release.");
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-[1400px] space-y-6 px-4 py-5 md:p-6">
      <AgentsNav />
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-xl font-semibold text-[var(--text)]">
            <Bot size={20} className="text-[var(--accent)]" /> Releases
          </h2>
          <p className="mt-1 max-w-3xl text-sm text-[var(--text-muted)]">
            Immutable agent recipes, state snapshots, runtimes and evidence.
            EFlux verifies provenance and bookkeeping; you decide which
            trade-offs fit your VPP.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => setShowCreate((value) => !value)}
            className="eflux-btn eflux-btn-primary h-9 px-3 text-sm"
          >
            <Plus size={15} /> New release
          </button>
        </div>
      </div>
      {error && (
        <p
          role="alert"
          className="rounded-lg border border-[color-mix(in_srgb,var(--danger)_35%,transparent)] bg-[var(--danger-soft)] px-3 py-2 text-sm text-[var(--danger)]"
        >
          {error}
        </p>
      )}

      {showCreate && (
        <DashboardCard>
          <CardTitle icon={Plus}>Create draft release</CardTitle>
          <p className="mb-5 text-sm text-[var(--text-muted)]">
            Draft metadata can change. Publishing freezes its content hash;
            upgrades become new releases instead of silently changing existing
            forks.
          </p>
          <form onSubmit={submit} className="space-y-5">
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <Field label="Release name">
                <input
                  required
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  className="eflux-input w-full"
                  placeholder="Solar shift agent"
                />
              </Field>
              <Field label="Semantic version">
                <input
                  required
                  value={version}
                  onChange={(event) => setVersion(event.target.value)}
                  className="eflux-input w-full font-mono"
                />
              </Field>
              <Field label="Market">
                <select
                  value={releaseMarket}
                  onChange={(event) =>
                    setReleaseMarket(event.target.value as Market)
                  }
                  className="eflux-input w-full"
                >
                  <option value="realprice">Realprice</option>
                  <option value="p2p">P2P</option>
                  <option value="hybrid">Hybrid</option>
                </select>
              </Field>
              <Field label="Visibility">
                <select
                  value={visibility}
                  onChange={(event) =>
                    setVisibility(event.target.value as Visibility)
                  }
                  className="eflux-input w-full"
                >
                  <option value="private">Private draft</option>
                  <option value="public">Public after publish</option>
                </select>
              </Field>
            </div>
            <Field label="Description">
              <textarea
                rows={3}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                className="eflux-input w-full resize-y"
                placeholder="Operating assumptions, intended assets and known limits."
              />
            </Field>
            <Field label="Evidence & portability badges">
              <input
                value={badges}
                onChange={(event) => setBadges(event.target.value)}
                className="eflux-input w-full"
                placeholder="Reproducible, Fresh-LLM Replay, External Dependency"
              />
            </Field>
            <div className="grid gap-4 lg:grid-cols-2">
              <JsonField
                label="Recipe JSON"
                value={recipeJson}
                onChange={setRecipeJson}
              />
              <JsonField
                label="State snapshot JSON"
                value={stateJson}
                onChange={setStateJson}
              />
              <JsonField
                label="Physical compatibility JSON"
                value={compatibilityJson}
                onChange={setCompatibilityJson}
              />
              <JsonField
                label="Runtime environment JSON"
                value={environmentJson}
                onChange={setEnvironmentJson}
              />
            </div>
            <div className="flex justify-end border-t border-[var(--border)] pt-4">
              <button
                disabled={creating}
                className="eflux-btn eflux-btn-primary h-9 px-4 text-sm disabled:opacity-50"
              >
                {creating ? "Creating…" : "Create draft"}
              </button>
            </div>
          </form>
        </DashboardCard>
      )}

      <DashboardCard>
        <CardTitle
          icon={PackageCheck}
          action={
            <span className="font-mono text-xs text-[var(--text-subtle)]">
              {visible.length} / {releases.length}
            </span>
          }
        >
          Release catalogue
        </CardTitle>
        <div className="mb-4 flex flex-wrap gap-2">
          <label className="eflux-input flex h-9 min-w-56 flex-1 items-center gap-2 px-3">
            <Search size={14} className="text-[var(--text-subtle)]" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="min-w-0 flex-1 bg-transparent text-sm outline-none"
              placeholder="Search name, algorithm or version"
            />
          </label>
          {(["all", "realprice", "p2p", "hybrid"] as MarketFilter[]).map(
            (item) => (
              <button
                key={item}
                type="button"
                onClick={() => setMarket(item)}
                className={`eflux-btn h-9 px-3 text-xs ${market === item ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent)]" : ""}`}
              >
                {humanize(item)}
              </button>
            ),
          )}
        </div>
        {loading ? (
          <p className="text-sm text-[var(--text-muted)]">Loading releases…</p>
        ) : visible.length === 0 ? (
          <EmptyState
            icon={Bot}
            title={
              releases.length
                ? "No releases match this filter"
                : "No agent releases yet"
            }
            body="Create a versioned recipe, attach evidence and publish it for other users to inspect or fork."
          />
        ) : (
          <div className="grid gap-3 lg:grid-cols-2">
            {visible.map((release) => (
              <Link
                key={release.id}
                to={`/agents/releases/${release.id}`}
                className="eflux-inset group rounded-xl p-4 transition-colors hover:bg-[var(--surface-hover)]"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="truncate font-semibold text-[var(--text)]">
                        {release.name}
                      </h2>
                      <span className="font-mono text-xs text-[var(--text-subtle)]">
                        v{release.version}
                      </span>
                      <StatusPill
                        tone={marketTone(release.market)}
                        className="py-0 text-[10px]"
                      >
                        {release.market}
                      </StatusPill>
                      <StatusPill
                        tone={statusTone(release.status)}
                        className="py-0 text-[10px]"
                      >
                        {release.status}
                      </StatusPill>
                    </div>
                    <p className="mt-2 line-clamp-2 text-sm text-[var(--text-muted)]">
                      {release.description || "No description supplied."}
                    </p>
                  </div>
                  <ChevronRight
                    size={18}
                    className="mt-0.5 shrink-0 text-[var(--text-subtle)] transition-transform group-hover:translate-x-0.5"
                  />
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  <StatusPill
                    tone={release.visibility === "public" ? "success" : "muted"}
                  >
                    {release.visibility}
                  </StatusPill>
                  {release.badges.slice(0, 4).map((badge) => (
                    <StatusPill
                      key={badge}
                      tone={
                        badge.toLowerCase().includes("external")
                          ? "amber"
                          : badge.toLowerCase().includes("verified")
                            ? "violet"
                            : "accent"
                      }
                      className="py-0 text-[10px]"
                    >
                      <ShieldCheck size={11} /> {badge}
                    </StatusPill>
                  ))}
                </div>
                <div className="mt-4 grid grid-cols-3 gap-3 border-t border-[var(--border)] pt-3 text-xs">
                  <Fact
                    label="Algorithm"
                    value={recordText(release.recipe, "algorithm", "custom")}
                  />
                  <Fact
                    label="Parent"
                    value={
                      release.parent_release_id === null
                        ? "original"
                        : String(release.parent_release_id)
                    }
                  />
                  <Fact
                    label="Content"
                    value={release.content_sha256?.slice(0, 10) || "draft"}
                  />
                </div>
              </Link>
            ))}
          </div>
        )}
      </DashboardCard>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block text-xs font-medium text-[var(--text-muted)]">
      <span className="mb-1.5 block">{label}</span>
      {children}
    </label>
  );
}
function JsonField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <Field label={label}>
      <textarea
        rows={7}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="eflux-input w-full resize-y font-mono text-xs"
        spellCheck={false}
      />
    </Field>
  );
}
function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-[var(--text-subtle)]">
        {label}
      </div>
      <div className="mt-1 truncate font-mono text-[var(--text)]">{value}</div>
    </div>
  );
}
