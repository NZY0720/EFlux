import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  Bot,
  BrainCircuit,
  CheckCircle2,
  Database,
  Download,
  FileCheck2,
  FileJson2,
  LoaderCircle,
  Play,
  Save,
} from "lucide-react";
import { Link, useParams } from "react-router-dom";

import {
  downloadBehaviorDataset,
  fetchBehaviorDataset,
  fetchTrainingRun,
  publishBehaviorDataset,
  trainBehaviorDataset,
  updateBehaviorDataset,
  type BehaviorDataset,
  type Provenance,
  type TrainingAlgorithm,
  type TrainingRun,
  type Visibility,
} from "../api/ecosystem";
import {
  CardTitle,
  DashboardCard,
  EmptyState,
  StatusPill,
} from "../components/DashboardCard";
import { AgentsNav } from "../components/WorkspaceNav";

type CompletenessKey =
  | "observation"
  | "action"
  | "execution_result"
  | "outcome"
  | "no_op"
  | "unfilled_orders"
  | "gateway_rejections";
type Completeness = Record<CompletenessKey, boolean>;
const completenessKeys: CompletenessKey[] = [
  "observation",
  "action",
  "execution_result",
  "outcome",
  "no_op",
  "unfilled_orders",
  "gateway_rejections",
];
const humanize = (value: string) =>
  value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
const marketTone = (market: BehaviorDataset["market"]) =>
  market === "realprice" ? "amber" : market === "p2p" ? "accent" : "violet";
const runTone = (status: TrainingRun["status"]) =>
  status === "succeeded"
    ? "success"
    : status === "failed"
      ? "danger"
      : status === "running"
        ? "accent"
        : "amber";
const formatDate = (value: string | null) =>
  value ? new Date(value).toLocaleString() : "—";
const formatBytes = (value: number | null) =>
  value === null
    ? "—"
    : value < 1024
      ? `${value} B`
      : value < 1_048_576
        ? `${(value / 1024).toFixed(1)} KB`
        : value < 1_073_741_824
          ? `${(value / 1_048_576).toFixed(1)} MB`
          : `${(value / 1_073_741_824).toFixed(1)} GB`;
const parseObject = (text: string): Record<string, unknown> => {
  const value = JSON.parse(text) as unknown;
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Manifest must be a JSON object.");
  return value as Record<string, unknown>;
};
const manifestText = (
  manifest: Record<string, unknown>,
  key: string,
  fallback: string,
) => (typeof manifest[key] === "string" ? (manifest[key] as string) : fallback);
function completenessOf(dataset: BehaviorDataset): Completeness {
  const raw = dataset.manifest.completeness;
  const source =
    raw && typeof raw === "object" && !Array.isArray(raw)
      ? (raw as Record<string, unknown>)
      : {};
  return Object.fromEntries(
    completenessKeys.map((key) => [key, source[key] === true]),
  ) as Completeness;
}

export default function BehaviorDatasetDetail() {
  const { id = "" } = useParams();
  const [dataset, setDataset] = useState<BehaviorDataset | null>(null);
  const [trainingRuns, setTrainingRuns] = useState<TrainingRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showEdit, setShowEdit] = useState(false);
  const [editDescription, setEditDescription] = useState("");
  const [editVisibility, setEditVisibility] = useState<Visibility>("private");
  const [editLicense, setEditLicense] = useState("");
  const [replacementPath, setReplacementPath] = useState("");
  const [manifestJson, setManifestJson] = useState("{}");
  const [algorithm, setAlgorithm] =
    useState<TrainingAlgorithm>("bc_warm_start");
  const [baseReleaseId, setBaseReleaseId] = useState("");
  const [trainingConfigJson, setTrainingConfigJson] = useState(
    JSON.stringify({ epochs: 20, learning_rate: 0.0003, seed: 7 }, null, 2),
  );

  const hydrate = (next: BehaviorDataset) => {
    setEditDescription(next.description ?? "");
    setEditVisibility(next.visibility);
    setEditLicense(next.license);
    setManifestJson(JSON.stringify(next.manifest, null, 2));
  };
  const load = async () => {
    try {
      const next = await fetchBehaviorDataset(id);
      setDataset(next);
      hydrate(next);
      setError(null);
    } catch (err) {
      setError((err as Error).message || "Unable to load this dataset.");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void load();
  }, [id]);
  useEffect(() => {
    if (
      !trainingRuns.some(
        (run) => run.status === "queued" || run.status === "running",
      )
    )
      return;
    const timer = window.setInterval(() => {
      void Promise.all(
        trainingRuns.map((run) =>
          run.status === "queued" || run.status === "running"
            ? fetchTrainingRun(run.id)
            : Promise.resolve(run),
        ),
      )
        .then(setTrainingRuns)
        .catch(() => undefined);
    }, 3_000);
    return () => window.clearInterval(timer);
  }, [trainingRuns]);

  const completeness = useMemo(
    () => (dataset ? completenessOf(dataset) : null),
    [dataset],
  );
  const completeCount = completeness
    ? Object.values(completeness).filter(Boolean).length
    : 0;
  const populationReady = dataset
    ? dataset.market === "realprice" || Boolean(dataset.manifest.population)
    : false;
  const publishReady = dataset?.download_available === true;
  const completenessReady =
    completeCount === completenessKeys.length && populationReady;
  const provenance = dataset
    ? (manifestText(
        dataset.manifest,
        "provenance",
        "self_reported",
      ) as Provenance)
    : "self_reported";

  const publish = async () => {
    setBusy("publish");
    setError(null);
    try {
      const updated = await publishBehaviorDataset(id);
      setDataset(updated);
      hydrate(updated);
    } catch (err) {
      setError((err as Error).message || "Unable to publish this dataset.");
    } finally {
      setBusy(null);
    }
  };
  const download = async () => {
    if (!dataset) return;
    setBusy("download");
    setError(null);
    try {
      await downloadBehaviorDataset(
        id,
        dataset.name.toLowerCase().replace(/[^a-z0-9]+/g, "-"),
      );
    } catch (err) {
      setError((err as Error).message || "Unable to download this dataset.");
    } finally {
      setBusy(null);
    }
  };
  const save = async (event: FormEvent) => {
    event.preventDefault();
    setBusy("save");
    setError(null);
    try {
      const updated = await updateBehaviorDataset(id, {
        description: editDescription.trim(),
        visibility: editVisibility,
        license: editLicense.trim(),
        manifest: parseObject(manifestJson),
        ...(replacementPath.trim()
          ? { artifact_path: replacementPath.trim() }
          : {}),
      });
      setDataset(updated);
      hydrate(updated);
      setReplacementPath("");
      setShowEdit(false);
    } catch (err) {
      setError((err as Error).message || "Unable to update this draft.");
    } finally {
      setBusy(null);
    }
  };
  const train = async (event: FormEvent) => {
    event.preventDefault();
    setBusy("train");
    setError(null);
    try {
      const config = parseObject(trainingConfigJson);
      if (algorithm === "ppo_finetune" && !baseReleaseId.trim())
        throw new Error(
          "Choose a BC warm-start or base release before PPO fine-tuning.",
        );
      if (baseReleaseId.trim())
        config.warm_start_release_id = baseReleaseId.trim();
      const created = await trainBehaviorDataset(id, { algorithm, config });
      setTrainingRuns((current) => [created, ...current]);
    } catch (err) {
      setError((err as Error).message || "Unable to start this training run.");
    } finally {
      setBusy(null);
    }
  };

  if (loading)
    return (
      <div className="mx-auto w-full max-w-[1400px] px-4 py-5 text-sm text-[var(--text-muted)] md:p-6">
        Loading behavior dataset…
      </div>
    );
  if (!dataset)
    return (
      <div className="mx-auto w-full max-w-2xl space-y-4 px-4 py-12 md:p-6">
        <EmptyState
          icon={Database}
          title="Behavior dataset unavailable"
          body={
            error ??
            "This dataset may have been removed or the link is invalid."
          }
        />
        <Link
          to="/agents/training-data"
          className="eflux-btn eflux-btn-primary h-9 px-4 text-sm"
        >
          Back to datasets
        </Link>
      </div>
    );

  return (
    <div className="mx-auto w-full max-w-[1400px] space-y-6 px-4 py-5 md:p-6">
      <AgentsNav />
      <Link
        to="/agents/training-data"
        className="inline-flex items-center gap-1.5 text-sm text-[var(--text-muted)] hover:text-[var(--text)]"
      >
        <ArrowLeft size={16} /> All training data
      </Link>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-xl font-semibold text-[var(--text)]">
              {dataset.name}
            </h2>
            <span className="font-mono text-sm text-[var(--text-subtle)]">
              v{dataset.version}
            </span>
            <StatusPill tone={marketTone(dataset.market)}>
              {dataset.market}
            </StatusPill>
            <StatusPill
              tone={dataset.status === "published" ? "success" : "amber"}
            >
              {dataset.status}
            </StatusPill>
            <StatusPill
              tone={
                provenance === "platform_verified"
                  ? "violet"
                  : provenance === "externally_attested"
                    ? "accent"
                    : "amber"
              }
            >
              {humanize(provenance)}
            </StatusPill>
          </div>
          <p className="mt-2 max-w-3xl text-sm text-[var(--text-muted)]">
            {dataset.description || "No description supplied."}
          </p>
          <p className="mt-2 font-mono text-[11px] text-[var(--text-subtle)]">
            dataset {dataset.id} · schema {dataset.schema_version} · updated{" "}
            {formatDate(dataset.updated_at)}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {dataset.status === "draft" && (
            <button
              type="button"
              onClick={() => setShowEdit((value) => !value)}
              className="eflux-btn h-9 px-3 text-sm"
            >
              <Save size={15} /> Edit draft
            </button>
          )}
          {dataset.status === "published" && dataset.download_available && (
            <button
              type="button"
              onClick={() => void download()}
              disabled={busy === "download"}
              className="eflux-btn h-9 px-3 text-sm disabled:opacity-50"
            >
              <Download size={15} />{" "}
              {busy === "download" ? "Preparing…" : "Download JSONL"}
            </button>
          )}
          {dataset.status === "draft" && (
            <button
              type="button"
              onClick={() => void publish()}
              disabled={busy === "publish" || !publishReady}
              title={
                publishReady
                  ? "Scan the artifact, then freeze verified manifest and hashes"
                  : "The artifact file is not available to the platform"
              }
              className="eflux-btn eflux-btn-primary h-9 px-3 text-sm disabled:opacity-50"
            >
              <FileCheck2 size={15} />{" "}
              {busy === "publish" ? "Publishing…" : "Publish immutable dataset"}
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
      {dataset.market !== "realprice" && (
        <div className="flex items-start gap-2 rounded-lg bg-[var(--warning-soft)] p-3 text-sm text-[var(--text)]">
          <AlertTriangle
            size={17}
            className="mt-0.5 shrink-0 text-[var(--warning)]"
          />
          P2P transferability depends on the recorded population, market depth,
          arrival order, scenario and seed. A non-empty{" "}
          <code className="font-mono">manifest.population</code> is required
          before publishing.
        </div>
      )}

      {showEdit && dataset.status === "draft" && (
        <DashboardCard>
          <CardTitle icon={Save}>Editable draft metadata</CardTitle>
          <form onSubmit={save} className="space-y-4">
            <Field label="Description">
              <textarea
                rows={3}
                value={editDescription}
                onChange={(event) => setEditDescription(event.target.value)}
                className="eflux-input w-full resize-y"
              />
            </Field>
            <div className="grid gap-4 md:grid-cols-3">
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
              <Field label="License">
                <input
                  required
                  value={editLicense}
                  onChange={(event) => setEditLicense(event.target.value)}
                  className="eflux-input w-full"
                />
              </Field>
              <Field label="Replacement artifact path (optional)">
                <input
                  value={replacementPath}
                  onChange={(event) => setReplacementPath(event.target.value)}
                  className="eflux-input w-full font-mono text-xs"
                />
              </Field>
            </div>
            <Field label="Manifest JSON">
              <textarea
                rows={14}
                value={manifestJson}
                onChange={(event) => setManifestJson(event.target.value)}
                className="eflux-input w-full resize-y font-mono text-xs"
                spellCheck={false}
              />
            </Field>
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
          <CardTitle icon={CheckCircle2}>
            Decision trajectory completeness
          </CardTitle>
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <StatusPill
              tone={
                completenessReady
                  ? "success"
                  : completeCount >= 4
                    ? "amber"
                    : "danger"
              }
            >
              {completeCount} / {completenessKeys.length} required sections
            </StatusPill>
            {dataset.market !== "realprice" && (
              <StatusPill tone={populationReady ? "success" : "danger"}>
                Population {populationReady ? "recorded" : "missing"}
              </StatusPill>
            )}
            <span className="text-xs text-[var(--text-subtle)]">
              No-ops, unfilled orders and gateway rejections are first-class
              training evidence.
            </span>
          </div>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {completeness &&
              completenessKeys.map((key) => (
                <div
                  key={key}
                  className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-xs ${completeness[key] ? "border-[color-mix(in_srgb,var(--success)_35%,transparent)] bg-[var(--success-soft)] text-[var(--success)]" : "border-[color-mix(in_srgb,var(--danger)_35%,transparent)] bg-[var(--danger-soft)] text-[var(--danger)]"}`}
                >
                  {completeness[key] ? (
                    <CheckCircle2 size={14} />
                  ) : (
                    <AlertTriangle size={14} />
                  )}
                  {humanize(key)}
                </div>
              ))}
          </div>
          {!publishReady && dataset.status === "draft" && (
            <p className="mt-4 text-xs text-[var(--warning)]">
              Upload or register the artifact before publishing. The server will
              scan its real contents and replace untrusted completeness claims.
            </p>
          )}
        </DashboardCard>
        <DashboardCard>
          <CardTitle icon={FileJson2}>Artifact facts</CardTitle>
          <dl className="space-y-4">
            <Fact
              label="Rows"
              value={dataset.row_count.toLocaleString()}
              mono
            />
            <Fact label="Size" value={formatBytes(dataset.size_bytes)} mono />
            <Fact label="License" value={dataset.license} />
            <Fact
              label="Asset profile"
              value={manifestText(
                dataset.manifest,
                "asset_profile",
                "not declared",
              )}
            />
            <Fact
              label="Source release"
              value={
                dataset.source_release_id === null
                  ? "Not linked"
                  : String(dataset.source_release_id)
              }
              mono
            />
            <Fact
              label="Artifact SHA-256"
              value={dataset.artifact_sha256 ?? "Assigned on publish"}
              mono
            />
            <Fact
              label="Content SHA-256"
              value={dataset.content_sha256 || "Assigned on publish"}
              mono
            />
          </dl>
        </DashboardCard>
      </div>

      <DashboardCard>
        <CardTitle icon={BrainCircuit}>Train a derived policy</CardTitle>
        <div className="mb-4 grid gap-3 md:grid-cols-2">
          <button
            type="button"
            onClick={() => {
              setAlgorithm("bc_warm_start");
              setTrainingConfigJson(
                JSON.stringify(
                  { epochs: 20, learning_rate: 0.0003, seed: 7 },
                  null,
                  2,
                ),
              );
            }}
            className={`rounded-xl border p-4 text-left ${algorithm === "bc_warm_start" ? "border-[var(--accent)] bg-[var(--accent-soft)]" : "border-[var(--border)]"}`}
          >
            <span className="font-semibold text-[var(--text)]">
              Create BC warm start
            </span>
            <span className="mt-1 block text-xs text-[var(--text-muted)]">
              Imitate actions in this static trajectory to produce an initial
              policy.
            </span>
          </button>
          <button
            type="button"
            onClick={() => {
              setAlgorithm("ppo_finetune");
              setTrainingConfigJson(
                JSON.stringify(
                  { sandbox_steps: 100000, learning_rate: 0.0001, seed: 7 },
                  null,
                  2,
                ),
              );
            }}
            className={`rounded-xl border p-4 text-left ${algorithm === "ppo_finetune" ? "border-[var(--violet)] bg-[color-mix(in_srgb,var(--violet)_10%,transparent)]" : "border-[var(--border)]"}`}
          >
            <span className="font-semibold text-[var(--text)]">
              Fine-tune with PPO
            </span>
            <span className="mt-1 block text-xs text-[var(--text-muted)]">
              Continue from a warm start inside the closed-loop EFlux sandbox.
            </span>
          </button>
        </div>
        <form
          onSubmit={train}
          className="grid items-end gap-4 lg:grid-cols-[minmax(180px,0.5fr)_minmax(320px,1.5fr)_auto]"
        >
          <Field
            label={
              algorithm === "ppo_finetune"
                ? "Base / BC release ID (required)"
                : "Optional base release ID"
            }
          >
            <input
              required={algorithm === "ppo_finetune"}
              value={baseReleaseId}
              onChange={(event) => setBaseReleaseId(event.target.value)}
              className="eflux-input w-full font-mono"
              placeholder="release id"
            />
          </Field>
          <Field label="Training config JSON">
            <textarea
              rows={5}
              value={trainingConfigJson}
              onChange={(event) => setTrainingConfigJson(event.target.value)}
              className="eflux-input w-full resize-y font-mono text-xs"
              spellCheck={false}
            />
          </Field>
          <button
            disabled={busy === "train" || dataset.status !== "published"}
            title={
              dataset.status === "published"
                ? "Queue sandbox training"
                : "Publish this complete dataset first"
            }
            className="eflux-btn eflux-btn-primary h-9 px-4 text-sm disabled:opacity-50"
          >
            {busy === "train" ? (
              <LoaderCircle
                size={15}
                className="animate-spin motion-reduce:animate-none"
              />
            ) : (
              <Play size={15} />
            )}
            {busy === "train"
              ? "Queuing…"
              : algorithm === "bc_warm_start"
                ? "Train BC"
                : "Fine-tune PPO"}
          </button>
        </form>
        <p className="mt-3 text-xs text-[var(--text-subtle)]">
          Static data initializes behavior cloning. PPO remains a closed-loop,
          on-policy step and does not train directly from a frozen trade log.
        </p>
      </DashboardCard>

      <DashboardCard>
        <CardTitle
          icon={BrainCircuit}
          action={
            <span className="font-mono text-xs text-[var(--text-subtle)]">
              {trainingRuns.length} started here
            </span>
          }
        >
          Training runs
        </CardTitle>
        {trainingRuns.length === 0 ? (
          <EmptyState
            icon={BrainCircuit}
            title="No training runs in this session"
            body="Publish a complete dataset, then create a BC warm start or fine-tune an existing warm start with PPO."
          />
        ) : (
          <div className="space-y-3">
            {trainingRuns.map((run) => (
              <TrainingRunCard key={run.id} run={run} />
            ))}
          </div>
        )}
      </DashboardCard>

      <DashboardCard>
        <CardTitle icon={FileJson2}>Published manifest</CardTitle>
        <pre className="max-h-[520px] overflow-auto rounded-lg border border-[var(--border)] bg-[var(--surface-inset)] p-4 text-xs text-[var(--text-muted)]">
          {JSON.stringify(dataset.manifest, null, 2)}
        </pre>
      </DashboardCard>
    </div>
  );
}

function TrainingRunCard({ run }: { run: TrainingRun }) {
  const active = run.status === "queued" || run.status === "running";
  const metrics = Object.entries(run.metrics ?? {});
  return (
    <section className="eflux-inset rounded-xl p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm text-[var(--text)]">
            training {run.id}
          </span>
          <StatusPill tone={runTone(run.status)}>
            {active && (
              <LoaderCircle
                size={12}
                className="animate-spin motion-reduce:animate-none"
              />
            )}
            {run.status}
          </StatusPill>
          <StatusPill
            tone={run.algorithm === "bc_warm_start" ? "accent" : "violet"}
          >
            {humanize(run.algorithm)}
          </StatusPill>
        </div>
        {run.output_release_id !== null && (
          <Link
            to={`/agents/releases/${run.output_release_id}`}
            className="eflux-btn h-8 px-3 text-xs"
          >
            <Bot size={13} /> Derived release {run.output_release_id}
          </Link>
        )}
      </div>
      {run.error && (
        <p className="mt-3 rounded-lg bg-[var(--danger-soft)] p-2 text-xs text-[var(--danger)]">
          {run.error}
        </p>
      )}
      {metrics.length > 0 && (
        <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-[var(--border)] pt-3 sm:grid-cols-3 lg:grid-cols-6">
          {metrics.map(([key, value]) => (
            <Fact
              key={key}
              label={humanize(key)}
              value={
                value === null
                  ? "—"
                  : typeof value === "number"
                    ? value.toLocaleString(undefined, {
                        maximumFractionDigits: 4,
                      })
                    : String(value)
              }
              mono
            />
          ))}
        </dl>
      )}
    </section>
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
