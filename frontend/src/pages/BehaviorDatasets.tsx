import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  ChevronRight,
  Database,
  FileJson2,
  Plus,
  Search,
} from "lucide-react";
import { Link, useNavigate } from "react-router-dom";

import {
  createBehaviorDataset,
  exportMarketSessionDataset,
  listBehaviorDatasets,
  uploadBehaviorDatasetArtifact,
  type BehaviorDataset,
  type CreateBehaviorDatasetInput,
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
type CreateMode = "market_session" | "artifact";
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
const initialCompleteness: Completeness = {
  observation: true,
  action: true,
  execution_result: true,
  outcome: true,
  no_op: true,
  unfilled_orders: true,
  gateway_rejections: true,
};
const marketTone = (market: Market) =>
  market === "realprice" ? "amber" : market === "p2p" ? "accent" : "violet";
const humanize = (value: string) =>
  value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
const parseObject = (text: string): Record<string, unknown> => {
  const value = JSON.parse(text) as unknown;
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Manifest metadata must be a JSON object.");
  return value as Record<string, unknown>;
};

function completenessOf(dataset: BehaviorDataset): Completeness {
  const raw = dataset.manifest.completeness;
  const value =
    raw && typeof raw === "object" && !Array.isArray(raw)
      ? (raw as Record<string, unknown>)
      : {};
  return Object.fromEntries(
    completenessKeys.map((key) => [key, value[key] === true]),
  ) as Completeness;
}
function completenessScore(dataset: BehaviorDataset): number {
  const values = Object.values(completenessOf(dataset));
  return Math.round((values.filter(Boolean).length / values.length) * 100);
}
function manifestText(
  dataset: BehaviorDataset,
  key: string,
  fallback: string,
): string {
  return typeof dataset.manifest[key] === "string"
    ? (dataset.manifest[key] as string)
    : fallback;
}

export default function BehaviorDatasets() {
  const navigate = useNavigate();
  const [datasets, setDatasets] = useState<BehaviorDataset[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [market, setMarket] = useState<MarketFilter>("all");
  const [showCreate, setShowCreate] = useState(false);
  const [createMode, setCreateMode] = useState<CreateMode>("market_session");
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [version, setVersion] = useState("1");
  const [description, setDescription] = useState("");
  const [datasetMarket, setDatasetMarket] = useState<Market>("realprice");
  const [visibility, setVisibility] = useState<Visibility>("private");
  const schemaVersion = "1" as const;
  const [marketSessionId, setMarketSessionId] = useState("");
  const [participantIds, setParticipantIds] = useState("");
  const [artifactPath, setArtifactPath] = useState("");
  const [artifactFile, setArtifactFile] = useState<File | null>(null);
  const [rowCount, setRowCount] = useState("");
  const [license, setLicense] = useState("CC-BY-4.0");
  const [sourceReleaseId, setSourceReleaseId] = useState("");
  const [completeness, setCompleteness] =
    useState<Completeness>(initialCompleteness);
  const [manifestJson, setManifestJson] = useState(
    JSON.stringify(
      {
        provenance: "self_reported",
        asset_profile: "battery_only",
        data_version: "v1",
        includes_private_data: false,
      },
      null,
      2,
    ),
  );

  const load = async () => {
    try {
      setDatasets(await listBehaviorDatasets());
      setError(null);
    } catch (err) {
      setError((err as Error).message || "Unable to load behavior datasets.");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void load();
  }, []);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return datasets.filter(
      (dataset) =>
        (market === "all" || dataset.market === market) &&
        (!needle ||
          [
            dataset.name,
            dataset.description ?? "",
            dataset.version,
            dataset.schema_version,
          ].some((value) => value.toLowerCase().includes(needle))),
    );
  }, [datasets, market, query]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setCreating(true);
    setError(null);
    try {
      const sourceReleaseIdValue = sourceReleaseId.trim()
        ? Number(sourceReleaseId.trim())
        : null;
      if (
        sourceReleaseIdValue !== null &&
        (!Number.isInteger(sourceReleaseIdValue) || sourceReleaseIdValue <= 0)
      )
        throw new Error("Enter a valid source release ID.");
      let created: BehaviorDataset;
      if (createMode === "market_session") {
        const selectedParticipants = participantIds
          .split(",")
          .map((value) => value.trim())
          .filter(Boolean)
          .map(Number);
        if (
          !marketSessionId.trim() ||
          selectedParticipants.some((value) => !Number.isInteger(value))
        )
          throw new Error("Enter a valid market session and participant IDs.");
        const marketSessionIdValue = Number(marketSessionId.trim());
        if (!Number.isInteger(marketSessionIdValue) || marketSessionIdValue <= 0)
          throw new Error("Enter a valid market session ID.");
        created = await exportMarketSessionDataset(marketSessionIdValue, {
          name: name.trim(),
          version: version.trim(),
          description: description.trim(),
          visibility,
          participant_ids: selectedParticipants.length
            ? selectedParticipants
            : null,
          license: license.trim(),
          source_release_id: sourceReleaseIdValue,
        });
      } else {
        if (!artifactFile && !artifactPath.trim())
          throw new Error("Choose a JSONL/JSONL.GZ file or enter a platform artifact path.");
        const metadata = parseObject(manifestJson);
        const body: CreateBehaviorDatasetInput = {
          name: name.trim(),
          version: version.trim(),
          description: description.trim(),
          market: datasetMarket,
          visibility,
          schema_version: schemaVersion,
          artifact_path: artifactFile ? null : artifactPath.trim(),
          row_count: rowCount ? Number(rowCount) : 0,
          license: license.trim(),
          source_release_id: sourceReleaseIdValue,
          manifest: { ...metadata, completeness },
        };
        created = await createBehaviorDataset(body);
        if (artifactFile)
          created = await uploadBehaviorDatasetArtifact(created.id, artifactFile);
      }
      navigate(`/agents/training-data/${created.id}`);
    } catch (err) {
      setError((err as Error).message || "Unable to create this dataset.");
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
            <Database size={20} className="text-[var(--violet)]" /> Training data
          </h2>
          <p className="mt-1 max-w-3xl text-sm text-[var(--text-muted)]">
            Decision trajectories preserve observations, no-ops, rejected and
            unfilled orders—not just successful trades—so users can inspect or
            train from the behavior that produced them.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => setShowCreate((value) => !value)}
            className="eflux-btn eflux-btn-primary h-9 px-3 text-sm"
          >
            <Plus size={15} /> New dataset
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
          <CardTitle icon={Plus}>Create decision trajectory dataset</CardTitle>
          <p className="mb-5 text-sm text-[var(--text-muted)]">
            Platform export derives provenance and completeness from persisted
            audit rows. External artifacts remain self-reported until a trusted
            attestation workflow verifies them.
          </p>
          <div className="mb-5 inline-flex overflow-hidden rounded-lg border border-[var(--border)]">
            {([
              ["market_session", "Export market session"],
              ["artifact", "Register artifact"],
            ] as const).map(([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => setCreateMode(value)}
                className={`px-4 py-2 text-sm ${createMode === value ? "bg-[var(--accent-soft)] text-[var(--accent)]" : "text-[var(--text-muted)]"}`}
              >
                {label}
              </button>
            ))}
          </div>
          <form onSubmit={submit} className="space-y-5">
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <Field label="Dataset name">
                <input
                  required
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  className="eflux-input w-full"
                  placeholder="July battery decisions"
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
              {createMode === "artifact" ? (
                <Field label="Market">
                  <select
                    value={datasetMarket}
                    onChange={(event) =>
                      setDatasetMarket(event.target.value as Market)
                    }
                    className="eflux-input w-full"
                  >
                    <option value="realprice">Realprice</option>
                    <option value="p2p">P2P</option>
                    <option value="hybrid">Hybrid</option>
                  </select>
                </Field>
              ) : (
                <Field label="Market session ID">
                  <input
                    required
                    min="1"
                    type="number"
                    value={marketSessionId}
                    onChange={(event) => setMarketSessionId(event.target.value)}
                    className="eflux-input w-full font-mono"
                  />
                </Field>
              )}
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
                placeholder="Collection window, strategy source, asset profile and known limitations."
              />
            </Field>
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              {createMode === "artifact" ? (
                <>
                  <Field label="Trajectory schema">
                    <input
                      required
                      value={schemaVersion}
                      readOnly
                      className="eflux-input w-full font-mono"
                    />
                  </Field>
                  <Field label="Upload trajectory artifact">
                    <input
                      type="file"
                      accept=".jsonl,.jsonl.gz,.gz,application/gzip,application/x-ndjson"
                      onChange={(event) =>
                        setArtifactFile(event.target.files?.[0] ?? null)
                      }
                      className="eflux-input w-full cursor-pointer text-xs"
                    />
                  </Field>
                  <Field label="Or existing platform path">
                    <input
                      value={artifactPath}
                      onChange={(event) => setArtifactPath(event.target.value)}
                      disabled={Boolean(artifactFile)}
                      className="eflux-input w-full font-mono text-xs"
                      placeholder="imports/run-123.jsonl.gz"
                    />
                  </Field>
                  <Field label="Rows (optional claim)">
                    <input
                      min="0"
                      type="number"
                      value={rowCount}
                      onChange={(event) => setRowCount(event.target.value)}
                      className="eflux-input w-full font-mono"
                    />
                  </Field>
                </>
              ) : (
                <Field label="Participant IDs (optional)">
                  <input
                    value={participantIds}
                    onChange={(event) => setParticipantIds(event.target.value)}
                    className="eflux-input w-full font-mono"
                    placeholder="-7, -8 · blank exports all owned"
                  />
                </Field>
              )}
              <Field label="License">
                <input
                  required
                  value={license}
                  onChange={(event) => setLicense(event.target.value)}
                  className="eflux-input w-full"
                />
              </Field>
              <Field label="Source release ID (optional)">
                <input
                  value={sourceReleaseId}
                  onChange={(event) => setSourceReleaseId(event.target.value)}
                  className="eflux-input w-full font-mono"
                />
              </Field>
            </div>
            {createMode === "artifact" && <fieldset className="rounded-xl border border-[var(--border)] p-4">
              <legend className="px-1 text-sm font-semibold text-[var(--text)]">
                Decision trajectory completeness
              </legend>
              <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                {completenessKeys.map((key) => (
                  <label
                    key={key}
                    className="flex items-center gap-2 rounded-lg bg-[var(--surface-inset)] px-3 py-2 text-xs text-[var(--text)]"
                  >
                    <input
                      type="checkbox"
                      checked={completeness[key]}
                      onChange={(event) =>
                        setCompleteness({
                          ...completeness,
                          [key]: event.target.checked,
                        })
                      }
                      className="accent-[var(--accent)]"
                    />{" "}
                    {humanize(key)}
                  </label>
                ))}
              </div>
            </fieldset>}
            {createMode === "artifact" && <Field label="Manifest metadata JSON">
              <textarea
                rows={7}
                value={manifestJson}
                onChange={(event) => setManifestJson(event.target.value)}
                className="eflux-input w-full resize-y font-mono text-xs"
                spellCheck={false}
              />
            </Field>}
            <div className="flex justify-end border-t border-[var(--border)] pt-4">
              <button
                disabled={creating}
                className="eflux-btn eflux-btn-primary h-9 px-4 text-sm disabled:opacity-50"
              >
                {creating
                  ? "Creating…"
                  : createMode === "market_session"
                    ? "Export verified draft"
                    : "Register self-reported draft"}
              </button>
            </div>
          </form>
        </DashboardCard>
      )}

      <DashboardCard>
        <CardTitle
          icon={FileJson2}
          action={
            <span className="font-mono text-xs text-[var(--text-subtle)]">
              {visible.length} / {datasets.length}
            </span>
          }
        >
          Dataset catalogue
        </CardTitle>
        <div className="mb-4 flex flex-wrap gap-2">
          <label className="eflux-input flex h-9 min-w-56 flex-1 items-center gap-2 px-3">
            <Search size={14} className="text-[var(--text-subtle)]" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="min-w-0 flex-1 bg-transparent text-sm outline-none"
              placeholder="Search name, schema or version"
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
          <p className="text-sm text-[var(--text-muted)]">Loading datasets…</p>
        ) : visible.length === 0 ? (
          <EmptyState
            icon={Database}
            title={
              datasets.length
                ? "No datasets match this filter"
                : "No behavior datasets yet"
            }
            body="Register an EFlux-generated trajectory, review completeness, then publish it for download or training."
          />
        ) : (
          <div className="grid gap-3 lg:grid-cols-2">
            {visible.map((dataset) => {
              const score = completenessScore(dataset);
              const provenance = manifestText(dataset, "provenance", "unrated");
              return (
                <Link
                  key={dataset.id}
                  to={`/agents/training-data/${dataset.id}`}
                  className="eflux-inset group rounded-xl p-4 transition-colors hover:bg-[var(--surface-hover)]"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <h2 className="truncate font-semibold text-[var(--text)]">
                          {dataset.name}
                        </h2>
                        <span className="font-mono text-xs text-[var(--text-subtle)]">
                          v{dataset.version}
                        </span>
                        <StatusPill
                          tone={marketTone(dataset.market)}
                          className="py-0 text-[10px]"
                        >
                          {dataset.market}
                        </StatusPill>
                        <StatusPill
                          tone={
                            dataset.status === "published" ? "success" : "amber"
                          }
                          className="py-0 text-[10px]"
                        >
                          {dataset.status}
                        </StatusPill>
                      </div>
                      <p className="mt-2 line-clamp-2 text-sm text-[var(--text-muted)]">
                        {dataset.description || "No description supplied."}
                      </p>
                    </div>
                    <ChevronRight
                      size={18}
                      className="mt-0.5 shrink-0 text-[var(--text-subtle)] transition-transform group-hover:translate-x-0.5"
                    />
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <StatusPill
                      tone={
                        score === 100
                          ? "success"
                          : score >= 50
                            ? "amber"
                            : "danger"
                      }
                    >
                      {score}% complete
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
                    <StatusPill tone="muted">
                      {dataset.schema_version}
                    </StatusPill>
                  </div>
                  <div className="mt-4 grid grid-cols-3 gap-3 border-t border-[var(--border)] pt-3 text-xs">
                    <Fact
                      label="Rows"
                      value={dataset.row_count?.toLocaleString() ?? "—"}
                    />
                    <Fact
                      label="Asset profile"
                      value={manifestText(
                        dataset,
                        "asset_profile",
                        "not declared",
                      )}
                    />
                    <Fact
                      label="Size"
                      value={formatBytes(dataset.size_bytes)}
                    />
                  </div>
                </Link>
              );
            })}
          </div>
        )}
      </DashboardCard>
    </div>
  );
}

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
