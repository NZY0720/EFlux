import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  Bot,
  Boxes,
  CheckCircle2,
  FlaskConical,
  GitFork,
  LoaderCircle,
  PackageCheck,
  Play,
  Plus,
  Rocket,
  Save,
  ShieldCheck,
} from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  createPopulationPack,
  createReleaseEvaluation,
  deployAgentRelease,
  forkAgentRelease,
  publishAgentRelease,
  updateAgentRelease,
  type AgentRelease,
  type CreatePopulationPackInput,
  type EvaluationKind,
  type PopulationPack,
  type ReleaseEvaluation,
  type Visibility,
} from "../api/ecosystem";
import {
  CardTitle,
  DashboardCard,
  EmptyState,
  StatusPill,
} from "../components/DashboardCard";
import { AgentsNav } from "../components/WorkspaceNav";
import { useAgentReleaseData } from "../features/agent-releases/hooks/useAgentReleaseData";

const humanize = (value: string) =>
  value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
const marketTone = (market: AgentRelease["market"]) =>
  market === "realprice" ? "amber" : market === "p2p" ? "accent" : "violet";
const releaseTone = (status: AgentRelease["status"]) =>
  status === "verified"
    ? "violet"
    : status === "published"
      ? "success"
      : "amber";
const runTone = (status: ReleaseEvaluation["status"]) =>
  status === "done"
    ? "success"
    : status === "failed"
      ? "danger"
      : status === "running"
        ? "accent"
        : "amber";
const formatDate = (value: string | null) =>
  value ? new Date(value).toLocaleString() : "—";
const formatMetric = (value: unknown) =>
  typeof value === "number"
    ? value.toLocaleString(undefined, { maximumFractionDigits: 4 })
    : value === null || value === undefined
      ? "—"
      : typeof value === "object"
        ? JSON.stringify(value)
        : String(value);
const parseObject = (text: string, label: string): Record<string, unknown> => {
  const value = JSON.parse(text) as unknown;
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error(`${label} must be a JSON object.`);
  return value as Record<string, unknown>;
};
const objectString = (record: Record<string, unknown>) =>
  JSON.stringify(record, null, 2);

export default function AgentReleaseDetail() {
  const { id = "" } = useParams();
  const releaseId = Number(id);
  const navigate = useNavigate();
  const {
    release,
    setRelease,
    evaluations,
    setEvaluations,
    populationPacks,
    setPopulationPacks,
    loading,
    loadError,
  } = useAgentReleaseData(releaseId);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showFork, setShowFork] = useState(false);
  const [forkName, setForkName] = useState("");
  const [forkVersion, setForkVersion] = useState("1");
  const [forkVisibility, setForkVisibility] = useState<Visibility>("private");
  const [showEdit, setShowEdit] = useState(false);
  const [showDeploy, setShowDeploy] = useState(false);
  const [editDescription, setEditDescription] = useState("");
  const [editVisibility, setEditVisibility] = useState<Visibility>("private");
  const [deployName, setDeployName] = useState("");
  const [deployProfile, setDeployProfile] = useState("battery-only");
  const [deployMode, setDeployMode] = useState<"shadow" | "paper" | "live">("shadow");
  const [riskAcknowledged, setRiskAcknowledged] = useState(false);
  const [recipeJson, setRecipeJson] = useState("{}");
  const [stateJson, setStateJson] = useState("{}");
  const [compatibilityJson, setCompatibilityJson] = useState("{}");
  const [environmentJson, setEnvironmentJson] = useState("{}");
  const [kind, setKind] = useState<EvaluationKind>("deterministic_replay");
  const [windowStart, setWindowStart] = useState("");
  const [windowEnd, setWindowEnd] = useState("");
  const [populationPackId, setPopulationPackId] = useState("");

  const hydrateEdit = (next: AgentRelease) => {
    setEditDescription(next.description ?? "");
    setEditVisibility(next.visibility);
    setRecipeJson(objectString(next.recipe));
    setStateJson(objectString(next.state));
    setCompatibilityJson(objectString(next.compatibility));
    setEnvironmentJson(objectString(next.environment));
  };

  useEffect(() => {
    if (!release) return;
    hydrateEdit(release);
    setKind(
      release.market === "realprice"
        ? "deterministic_replay"
        : release.market === "p2p"
          ? "p2p_tournament"
          : "hybrid_evaluation",
    );
  }, [release]);

  const isPopulationRun =
    kind === "p2p_tournament" || kind === "hybrid_evaluation";
  const selectedPack = useMemo(
    () => populationPacks.find((pack) => String(pack.id) === populationPackId),
    [populationPackId, populationPacks],
  );

  const publish = async () => {
    setBusy("publish");
    setError(null);
    try {
      const updated = await publishAgentRelease(releaseId);
      setRelease(updated);
      hydrateEdit(updated);
    } catch (err) {
      setError((err as Error).message || "Unable to publish this release.");
    } finally {
      setBusy(null);
    }
  };
  const fork = async (event: FormEvent) => {
    event.preventDefault();
    setBusy("fork");
    setError(null);
    try {
      const created = await forkAgentRelease(releaseId, {
        name: forkName.trim(),
        version: forkVersion.trim(),
        visibility: forkVisibility,
      });
      navigate(`/agents/releases/${created.id}`);
    } catch (err) {
      setError((err as Error).message || "Unable to fork this release.");
    } finally {
      setBusy(null);
    }
  };
  const save = async (event: FormEvent) => {
    event.preventDefault();
    setBusy("save");
    setError(null);
    try {
      const updated = await updateAgentRelease(releaseId, {
        description: editDescription.trim(),
        visibility: editVisibility,
        recipe: parseObject(recipeJson, "Recipe"),
        state: parseObject(stateJson, "State"),
        compatibility: parseObject(compatibilityJson, "Compatibility"),
        environment: parseObject(environmentJson, "Environment"),
      });
      setRelease(updated);
      hydrateEdit(updated);
      setShowEdit(false);
    } catch (err) {
      setError((err as Error).message || "Unable to update this draft.");
    } finally {
      setBusy(null);
    }
  };
  const deploy = async (event: FormEvent) => {
    event.preventDefault();
    setBusy("deploy");
    setError(null);
    try {
      const deployment = await deployAgentRelease(releaseId, {
        name: deployName.trim(),
        profile_id: deployProfile,
        params: {},
        mode: deployMode,
        risk_acknowledged: riskAcknowledged,
        credential_bindings: [],
      });
      navigate(`/vpps/${deployment.id}`);
    } catch (err) {
      setError((err as Error).message || "Unable to deploy this release.");
    } finally {
      setBusy(null);
    }
  };
  const evaluate = async (event: FormEvent) => {
    event.preventDefault();
    setBusy("evaluate");
    setError(null);
    try {
      const config: Record<string, unknown> = {};
      if (windowStart) config.window_start = windowStart;
      if (windowEnd) config.window_end = windowEnd;
      if (isPopulationRun && populationPackId)
        config.population_pack_id = populationPackId;
      const created = await createReleaseEvaluation(releaseId, {
        kind,
        config,
      });
      setEvaluations((current) => [
        created,
        ...current.filter((item) => item.id !== created.id),
      ]);
    } catch (err) {
      setError((err as Error).message || "Unable to start this evaluation.");
    } finally {
      setBusy(null);
    }
  };

  if (loading)
    return (
      <div className="mx-auto w-full max-w-[1400px] px-4 py-5 text-sm text-[var(--text-muted)] md:p-6">
        Loading agent release…
      </div>
    );
  if (!release)
    return (
      <div className="mx-auto w-full max-w-2xl space-y-4 px-4 py-12 md:p-6">
        <EmptyState
          icon={Bot}
          title="Agent release unavailable"
          body={
            loadError ??
            "This release may have been removed or the link is invalid."
          }
        />
        <Link
          to="/agents"
          className="eflux-btn eflux-btn-primary h-9 px-4 text-sm"
        >
          Back to releases
        </Link>
      </div>
    );

  return (
    <div className="mx-auto w-full max-w-[1400px] space-y-6 px-4 py-5 md:p-6">
      <AgentsNav />
      <Link
        to="/agents"
        className="inline-flex items-center gap-1.5 text-sm text-[var(--text-muted)] hover:text-[var(--text)]"
      >
        <ArrowLeft size={16} /> All agent releases
      </Link>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-xl font-semibold text-[var(--text)]">
              {release.name}
            </h2>
            <span className="font-mono text-sm text-[var(--text-subtle)]">
              v{release.version}
            </span>
            <StatusPill tone={marketTone(release.market)}>
              {release.market}
            </StatusPill>
            <StatusPill tone={releaseTone(release.status)}>
              {release.status}
            </StatusPill>
            <StatusPill
              tone={release.visibility === "public" ? "success" : "muted"}
            >
              {release.visibility}
            </StatusPill>
          </div>
          <p className="mt-2 max-w-3xl text-sm text-[var(--text-muted)]">
            {release.description || "No description supplied."}
          </p>
          <p className="mt-2 font-mono text-[11px] text-[var(--text-subtle)]">
            release {release.id} · updated {formatDate(release.updated_at)}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {release.status === "draft" && (
            <button
              type="button"
              onClick={() => setShowEdit((value) => !value)}
              className="eflux-btn h-9 px-3 text-sm"
            >
              <Save size={15} /> Edit draft
            </button>
          )}
          <button
            type="button"
            onClick={() => {
              setForkName(`${release.name} fork`);
              setShowFork((value) => !value);
            }}
            className="eflux-btn h-9 px-3 text-sm"
          >
            <GitFork size={15} /> Fork
          </button>
          {release.status !== "draft" && (
            <button
              type="button"
              onClick={() => {
                setDeployName(`${release.name} deployment`);
                setShowDeploy((value) => !value);
              }}
              className="eflux-btn h-9 px-3 text-sm"
            >
              <Rocket size={15} /> Deploy
            </button>
          )}
          {release.status === "draft" && (
            <button
              type="button"
              onClick={() => void publish()}
              disabled={busy === "publish"}
              className="eflux-btn eflux-btn-primary h-9 px-3 text-sm disabled:opacity-50"
            >
              <PackageCheck size={15} />{" "}
              {busy === "publish" ? "Publishing…" : "Publish immutable release"}
            </button>
          )}
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

      {showFork && (
        <DashboardCard>
          <CardTitle icon={GitFork}>Fork as an independent draft</CardTitle>
          <div className="mb-4 flex items-start gap-2 rounded-lg bg-[var(--warning-soft)] p-3 text-sm text-[var(--text)]">
            <AlertTriangle
              size={17}
              className="mt-0.5 shrink-0 text-[var(--warning)]"
            />
            The fork preserves lineage and copies declared recipe/state
            references. Positions, learning state, memory, credentials and logs
            remain independent.
          </div>
          <form onSubmit={fork} className="flex flex-wrap items-end gap-3">
            <Field label="Fork name" className="min-w-64 flex-1">
              <input
                required
                value={forkName}
                onChange={(event) => setForkName(event.target.value)}
                className="eflux-input w-full"
              />
            </Field>
            <Field label="Starting version" className="w-36">
              <input
                required
                value={forkVersion}
                onChange={(event) => setForkVersion(event.target.value)}
                className="eflux-input w-full font-mono"
              />
            </Field>
            <Field label="Visibility" className="w-36">
              <select
                value={forkVisibility}
                onChange={(event) =>
                  setForkVisibility(event.target.value as Visibility)
                }
                className="eflux-input w-full"
              >
                <option value="private">Private</option>
                <option value="public">Public</option>
              </select>
            </Field>
            <button
              disabled={busy === "fork"}
              className="eflux-btn eflux-btn-primary h-9 px-4 text-sm disabled:opacity-50"
            >
              {busy === "fork" ? "Forking…" : "Create fork"}
            </button>
          </form>
        </DashboardCard>
      )}

      {showDeploy && release.status !== "draft" && (
        <DashboardCard>
          <CardTitle icon={Rocket}>Deploy an independent instance</CardTitle>
          <p className="mb-4 text-sm text-[var(--text-muted)]">
            Shadow and paper modes record decisions without sending orders. Live mode requires
            completed platform evidence and an explicit risk acknowledgement.
          </p>
          <form onSubmit={deploy} className="grid items-end gap-4 md:grid-cols-2 xl:grid-cols-5">
            <Field label="Deployment name" className="xl:col-span-2">
              <input required value={deployName} onChange={(event) => setDeployName(event.target.value)} className="eflux-input w-full" />
            </Field>
            <Field label="Asset profile">
              <select value={deployProfile} onChange={(event) => setDeployProfile(event.target.value)} className="eflux-input w-full">
                <option value="battery-only">Battery-only</option>
                <option value="residential-pv-battery">Residential PV + Battery</option>
                <option value="commercial-load-battery">Commercial Load + Battery</option>
                <option value="industrial-flexible-load">Industrial Flexible Load</option>
                <option value="renewable-generator">Renewable Generator</option>
              </select>
            </Field>
            <Field label="Mode">
              <select value={deployMode} onChange={(event) => setDeployMode(event.target.value as "shadow" | "paper" | "live")} className="eflux-input w-full">
                <option value="shadow">Shadow</option>
                <option value="paper">Paper</option>
                <option value="live">Live</option>
              </select>
            </Field>
            <button disabled={busy === "deploy" || (deployMode === "live" && !riskAcknowledged)} className="eflux-btn eflux-btn-primary h-9 px-4 text-sm disabled:opacity-50">
              {busy === "deploy" ? "Deploying…" : "Create deployment"}
            </button>
            {deployMode === "live" && (
              <label className="flex items-center gap-2 text-sm text-[var(--text-muted)] md:col-span-2 xl:col-span-5">
                <input type="checkbox" checked={riskAcknowledged} onChange={(event) => setRiskAcknowledged(event.target.checked)} />
                I acknowledge that this release can submit live market orders under my account.
              </label>
            )}
          </form>
        </DashboardCard>
      )}

      {showEdit && release.status === "draft" && (
        <DashboardCard>
          <CardTitle icon={Save}>Editable draft definition</CardTitle>
          <form onSubmit={save} className="space-y-4">
            <Field label="Description">
              <textarea
                rows={3}
                value={editDescription}
                onChange={(event) => setEditDescription(event.target.value)}
                className="eflux-input w-full resize-y"
              />
            </Field>
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="Visibility">
                <select
                  value={editVisibility}
                  onChange={(event) =>
                    setEditVisibility(event.target.value as Visibility)
                  }
                  className="eflux-input w-full"
                >
                  <option value="private">Private</option>
                  <option value="public">Public after publishing</option>
                </select>
              </Field>
              <Field label="Evidence badges">
                <div className="eflux-input flex w-full items-center text-sm text-[var(--text-muted)]">
                  Assigned from completed platform evidence
                </div>
              </Field>
            </div>
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
                label="Compatibility JSON"
                value={compatibilityJson}
                onChange={setCompatibilityJson}
              />
              <JsonField
                label="Environment JSON"
                value={environmentJson}
                onChange={setEnvironmentJson}
              />
            </div>
            <div className="flex justify-end">
              <button
                disabled={busy === "save"}
                className="eflux-btn eflux-btn-primary h-9 px-4 text-sm disabled:opacity-50"
              >
                <Save size={15} /> {busy === "save" ? "Saving…" : "Save draft"}
              </button>
            </div>
          </form>
        </DashboardCard>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <DashboardCard className="lg:col-span-2">
          <CardTitle icon={Boxes}>Recipe, state & runtime</CardTitle>
          <div className="grid gap-x-6 gap-y-4 sm:grid-cols-2 lg:grid-cols-3">
            <Fact
              label="Algorithm"
              value={textValue(release.recipe.algorithm, "custom")}
            />
            <Fact
              label="Agent protocol"
              value={textValue(release.recipe.protocol_version, "not declared")}
              mono
            />
            <Fact
              label="Observation schema"
              value={textValue(
                release.recipe.observation_schema_version,
                "not declared",
              )}
              mono
            />
            <Fact
              label="Action schema"
              value={textValue(
                release.recipe.action_schema_version,
                "not declared",
              )}
              mono
            />
            <Fact
              label="Online learning"
              value={
                release.recipe.online_learning === true
                  ? "Enabled — state snapshot included"
                  : "Disabled / not declared"
              }
            />
            <Fact
              label="Fallback"
              value={textValue(
                release.recipe.fallback_strategy,
                "not declared",
              )}
            />
            <Fact
              label="Portability"
              value={textValue(release.compatibility.portability, "not rated")}
            />
            <Fact
              label="Runtime"
              value={textValue(release.environment.runtime, "not declared")}
            />
            <Fact
              label="Parent release"
              value={
                release.parent_release_id === null
                  ? "Original recipe"
                  : String(release.parent_release_id)
              }
              mono
            />
          </div>
          <div className="mt-5 grid gap-3 md:grid-cols-2">
            {[
              ["Canonical recipe", release.recipe],
              ["State snapshot", release.state],
              ["Compatibility", release.compatibility],
              ["Environment", release.environment],
            ].map(([label, value]) => (
              <details
                key={String(label)}
                className="rounded-lg border border-[var(--border)] bg-[var(--surface-inset)]"
              >
                <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-[var(--text)]">
                  {String(label)}
                </summary>
                <pre className="max-h-72 overflow-auto border-t border-[var(--border)] p-3 text-xs text-[var(--text-muted)]">
                  {JSON.stringify(value, null, 2)}
                </pre>
              </details>
            ))}
          </div>
        </DashboardCard>
        <DashboardCard>
          <CardTitle icon={ShieldCheck}>Identity & portability</CardTitle>
          <div className="flex flex-wrap gap-2">
            {release.badges.length ? (
              release.badges.map((badge) => (
                <StatusPill
                  key={badge}
                  tone={
                    badge.toLowerCase().includes("external")
                      ? "amber"
                      : badge.toLowerCase().includes("verified")
                        ? "violet"
                        : "accent"
                  }
                >
                  {badge}
                </StatusPill>
              ))
            ) : (
              <StatusPill>No badges claimed</StatusPill>
            )}
          </div>
          <div className="mt-5 space-y-4">
            <Fact
              label="Content SHA-256"
              value={release.content_sha256 || "Assigned on publish"}
              mono
            />
            <Fact label="Published" value={formatDate(release.published_at)} />
            <Fact label="Created" value={formatDate(release.created_at)} />
          </div>
        </DashboardCard>
      </div>

      <DashboardCard>
        <CardTitle icon={FlaskConical}>Run evaluation</CardTitle>
        {release.market !== "realprice" && (
          <div className="mb-4 flex items-start gap-2 rounded-lg bg-[var(--warning-soft)] p-3 text-sm text-[var(--text)]">
            <AlertTriangle
              size={17}
              className="mt-0.5 shrink-0 text-[var(--warning)]"
            />
            P2P outcomes depend on the participant population. Use a closed-loop
            population pack; static historical P2P prices are not presented as a
            replay benchmark.
          </div>
        )}
        <form
          onSubmit={evaluate}
          className="grid items-end gap-4 md:grid-cols-2 xl:grid-cols-5"
        >
          <Field label="Evaluation kind">
            <select
              value={kind}
              onChange={(event) =>
                setKind(event.target.value as EvaluationKind)
              }
              className="eflux-input w-full"
            >
              <option value="deterministic_replay">Deterministic replay</option>
              <option value="fresh_llm_replay">Fresh-LLM replay</option>
              <option value="forward_shadow">Forward shadow</option>
              <option value="verified_live">Verified live</option>
              <option value="p2p_tournament">P2P tournament</option>
              <option value="hybrid_evaluation">Hybrid evaluation</option>
            </select>
          </Field>
          <Field label="Provenance">
            <div className="eflux-input flex w-full items-center text-sm text-[var(--text-muted)]">
              Assigned by the platform worker
            </div>
          </Field>
          <Field label="Window start">
            <input
              type="date"
              required={kind === "fresh_llm_replay"}
              value={windowStart}
              onChange={(event) => setWindowStart(event.target.value)}
              className="eflux-input w-full"
            />
          </Field>
          <Field label="Window end">
            <input
              type="date"
              required={kind === "fresh_llm_replay"}
              value={windowEnd}
              onChange={(event) => setWindowEnd(event.target.value)}
              className="eflux-input w-full"
            />
          </Field>
          <div>
            <Field label="Population pack">
              <select
                value={populationPackId}
                onChange={(event) => setPopulationPackId(event.target.value)}
                disabled={!isPopulationRun}
                className="eflux-input w-full disabled:opacity-50"
              >
                <option value="">
                  {isPopulationRun
                    ? "All platform packs"
                    : "Not used"}
                </option>
                {populationPacks.map((pack) => (
                  <option key={pack.id} value={String(pack.id)}>
                    {pack.name} · v{pack.version}
                  </option>
                ))}
              </select>
            </Field>
            <button
              disabled={busy === "evaluate"}
              className="eflux-btn eflux-btn-primary mt-2 h-9 w-full px-4 text-sm disabled:opacity-50"
            >
              {busy === "evaluate" ? (
                <LoaderCircle
                  size={15}
                  className="animate-spin motion-reduce:animate-none"
                />
              ) : (
                <Play size={15} />
              )}{" "}
              {busy === "evaluate" ? "Queuing…" : "Run"}
            </button>
          </div>
        </form>
        {kind === "fresh_llm_replay" && (
          <p className="mt-3 text-xs text-[var(--text-subtle)]">
            Fresh-LLM evidence records current-model calls and costs but is not
            labeled deterministic or reproducible.
          </p>
        )}
        {(release.market === "p2p" || release.market === "hybrid") && (
          <PopulationPackCreator
            market={release.market}
            onCreated={(pack) => {
              setPopulationPacks((current) => [...current, pack]);
              setPopulationPackId(String(pack.id));
            }}
          />
        )}
        {isPopulationRun && !selectedPack && (
          <p className="mt-3 text-xs text-[var(--text-subtle)]">
            The default runs every platform population pack across the same seeds.
            Select one pack only for a targeted diagnostic run.
          </p>
        )}
      </DashboardCard>

      <DashboardCard>
        <CardTitle
          icon={CheckCircle2}
          action={
            <span className="font-mono text-xs text-[var(--text-subtle)]">
              {evaluations.length} runs
            </span>
          }
        >
          Evaluation results
        </CardTitle>
        {evaluations.length === 0 ? (
          <EmptyState
            icon={FlaskConical}
            title="No evaluation results yet"
            body="Run a replay, forward shadow, live verification or population experiment. The platform shows evidence without assigning a universal definition of better."
          />
        ) : (
          <div className="space-y-3">
            {evaluations.map((evaluation) => (
              <EvaluationCard key={evaluation.id} evaluation={evaluation} />
            ))}
          </div>
        )}
      </DashboardCard>
    </div>
  );
}

function EvaluationCard({ evaluation }: { evaluation: ReleaseEvaluation }) {
  const metrics = Object.entries(evaluation.metrics ?? {});
  const active =
    evaluation.status === "queued" || evaluation.status === "running";
  return (
    <section className="eflux-inset rounded-xl p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-sm text-[var(--text)]">
              evaluation {evaluation.id}
            </span>
            <StatusPill tone={runTone(evaluation.status)}>
              {active && (
                <LoaderCircle
                  size={12}
                  className="animate-spin motion-reduce:animate-none"
                />
              )}
              {evaluation.status}
            </StatusPill>
            <StatusPill tone="violet">{humanize(evaluation.kind)}</StatusPill>
            <StatusPill
              tone={
                evaluation.provenance === "platform_verified"
                  ? "success"
                  : evaluation.provenance === "externally_attested"
                    ? "accent"
                    : "amber"
              }
            >
              {humanize(evaluation.provenance)}
            </StatusPill>
          </div>
          <p className="mt-2 text-xs text-[var(--text-subtle)]">
            queued {formatDate(evaluation.created_at)} · finished{" "}
            {formatDate(evaluation.finished_at)}
          </p>
        </div>
        {evaluation.evidence_sha256 && (
          <span
            title={evaluation.evidence_sha256}
            className="max-w-52 truncate font-mono text-[10px] text-[var(--text-subtle)]"
          >
            evidence {evaluation.evidence_sha256}
          </span>
        )}
      </div>
      {evaluation.error && (
        <p className="mt-3 rounded-lg bg-[var(--danger-soft)] p-2 text-xs text-[var(--danger)]">
          {evaluation.error}
        </p>
      )}
      {metrics.length > 0 && (
        <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-[var(--border)] pt-3 sm:grid-cols-3 lg:grid-cols-6">
          {metrics.map(([key, value]) => (
            <Fact
              key={key}
              label={humanize(key)}
              value={formatMetric(value)}
              mono
            />
          ))}
        </dl>
      )}
    </section>
  );
}

function PopulationPackCreator({
  market,
  onCreated,
}: {
  market: "p2p" | "hybrid";
  onCreated: (pack: PopulationPack) => void;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [version, setVersion] = useState("1");
  const [description, setDescription] = useState("");
  const [visibility, setVisibility] = useState<Visibility>("public");
  const [specJson, setSpecJson] = useState(
    JSON.stringify(
      {
        market,
        seed_count: 10,
        scenario_tags: ["balanced", "baseline"],
        roster: { truthful: 4, zi: 4, market_maker: 1 },
      },
      null,
      2,
    ),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body: CreatePopulationPackInput = {
        name: name.trim(),
        version: version.trim(),
        description: description.trim(),
        visibility,
        spec: parseObject(specJson, "Population spec"),
      };
      const pack = await createPopulationPack(body);
      onCreated(pack);
      setOpen(false);
    } catch (err) {
      setError((err as Error).message || "Unable to create population pack.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="mt-4 border-t border-[var(--border)] pt-4">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="inline-flex items-center gap-1.5 text-xs font-medium text-[var(--accent)] hover:underline"
      >
        <Plus size={13} /> Create a reusable population pack
      </button>
      {open && (
        <form
          onSubmit={submit}
          className="mt-3 grid items-end gap-3 rounded-lg border border-[var(--border)] bg-[var(--surface-inset)] p-3 md:grid-cols-2 xl:grid-cols-4"
        >
          <Field label="Pack name">
            <input
              required
              value={name}
              onChange={(event) => setName(event.target.value)}
              className="eflux-input w-full"
            />
          </Field>
          <Field label="Version">
            <input
              required
              value={version}
              onChange={(event) => setVersion(event.target.value)}
              className="eflux-input w-full font-mono"
            />
          </Field>
          <Field label="Description">
            <input
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              className="eflux-input w-full"
            />
          </Field>
          <Field label="Visibility">
            <select
              value={visibility}
              onChange={(event) =>
                setVisibility(event.target.value as Visibility)
              }
              className="eflux-input w-full"
            >
              <option value="public">Public</option>
              <option value="private">Private</option>
            </select>
          </Field>
          <div className="md:col-span-2 xl:col-span-4">
            <JsonField
              label="Population specification"
              value={specJson}
              onChange={setSpecJson}
            />
          </div>
          <div className="flex justify-end md:col-span-2 xl:col-span-4">
            <button
              disabled={busy}
              className="eflux-btn h-8 px-3 text-xs disabled:opacity-50"
            >
              {busy ? "Creating…" : "Create published pack"}
            </button>
          </div>
          {error && (
            <p className="text-xs text-[var(--danger)] md:col-span-2 xl:col-span-4">
              {error}
            </p>
          )}
        </form>
      )}
    </div>
  );
}

const textValue = (value: unknown, fallback: string) =>
  typeof value === "string" ? value : fallback;
function Field({
  label,
  children,
  className = "",
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <label
      className={`block text-xs font-medium text-[var(--text-muted)] ${className}`}
    >
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
function Fact({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="min-w-0">
      <dt className="text-[10px] uppercase tracking-wide text-[var(--text-subtle)]">
        {label}
      </dt>
      <dd
        className={`mt-1 break-words text-sm text-[var(--text)] ${mono ? "font-mono tabular-nums" : ""}`}
      >
        {value}
      </dd>
    </div>
  );
}
