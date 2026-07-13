import { api } from "./client";

export type ArtifactId = number | string;
export type Market = "realprice" | "p2p" | "hybrid";
export type Visibility = "public" | "private";
export type ReleaseStatus = "draft" | "published" | "verified";
export type Provenance =
  | "platform_verified"
  | "externally_attested"
  | "self_reported";
export type EvaluationKind =
  | "deterministic_replay"
  | "fresh_llm_replay"
  | "forward_shadow"
  | "verified_live"
  | "p2p_tournament"
  | "hybrid_evaluation";
export type EvaluationStatus = "queued" | "running" | "done" | "failed";
export type TrainingStatus = "queued" | "running" | "succeeded" | "failed";
export type TrainingAlgorithm = "bc_warm_start" | "ppo_finetune";

export interface AgentRelease {
  id: ArtifactId;
  owner_id: ArtifactId;
  name: string;
  version: string;
  description: string;
  market: Market;
  visibility: Visibility;
  recipe: Record<string, unknown>;
  state: Record<string, unknown>;
  compatibility: Record<string, unknown>;
  environment: Record<string, unknown>;
  badges: string[];
  status: ReleaseStatus;
  parent_release_id: ArtifactId | null;
  content_sha256: string | null;
  created_at: string;
  updated_at: string;
  published_at: string | null;
}

export interface CreateAgentReleaseInput {
  name: string;
  version: string;
  description?: string;
  market: Market;
  visibility: Visibility;
  recipe: Record<string, unknown>;
  state: Record<string, unknown>;
  compatibility: Record<string, unknown>;
  environment: Record<string, unknown>;
  badges: string[];
}

export type UpdateAgentReleaseInput = Partial<CreateAgentReleaseInput>;

export interface ForkAgentReleaseInput {
  name?: string;
  version?: string;
  visibility?: Visibility;
}

export interface DeployAgentReleaseInput {
  name: string;
  profile_id: string;
  params: Record<string, unknown>;
  mode: "shadow" | "paper" | "live";
  risk_acknowledged: boolean;
  credential_bindings: string[];
}

export interface AgentReleaseDeployment {
  id: number;
  vpp_id: number;
  release_id: number;
  release_content_sha256: string;
  name: string;
  mode: "shadow" | "paper" | "live";
  params: Record<string, unknown>;
}

export interface ReleaseEvaluation {
  id: ArtifactId;
  release_id: ArtifactId;
  requested_by_id: ArtifactId;
  kind: EvaluationKind;
  provenance: Provenance;
  status: EvaluationStatus;
  config: Record<string, unknown>;
  metrics: Record<string, unknown>;
  evidence: Record<string, unknown> | null;
  evidence_sha256: string | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface CreateReleaseEvaluationInput {
  kind: EvaluationKind;
  config: Record<string, unknown>;
}

export interface BehaviorDataset {
  id: ArtifactId;
  owner_id: ArtifactId;
  name: string;
  version: string;
  description: string;
  market: Market;
  visibility: Visibility;
  schema_version: string;
  manifest: Record<string, unknown>;
  download_available: boolean;
  artifact_sha256: string | null;
  size_bytes: number;
  row_count: number;
  license: string;
  parent_dataset_id: ArtifactId | null;
  source_release_id: ArtifactId | null;
  status: ReleaseStatus;
  content_sha256: string | null;
  created_at: string;
  updated_at: string;
  published_at: string | null;
}

export interface CreateBehaviorDatasetInput {
  name: string;
  version: string;
  description?: string;
  market: Market;
  visibility: Visibility;
  schema_version: string;
  manifest: Record<string, unknown>;
  artifact_path?: string | null;
  row_count?: number;
  license: string;
  parent_dataset_id?: ArtifactId | null;
  source_release_id?: ArtifactId | null;
}

export interface ExportMarketSessionDatasetInput {
  name: string;
  version: string;
  description?: string;
  visibility: Visibility;
  participant_ids?: number[] | null;
  source_release_id?: ArtifactId | null;
  license: string;
}

export type UpdateBehaviorDatasetInput = Partial<CreateBehaviorDatasetInput>;

export interface TrainingRun {
  id: ArtifactId;
  dataset_id: ArtifactId;
  owner_id: ArtifactId;
  algorithm: TrainingAlgorithm;
  status: TrainingStatus;
  config: Record<string, unknown>;
  metrics: Record<string, unknown>;
  output_release_id: ArtifactId | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface CreateTrainingRunInput {
  algorithm: TrainingAlgorithm;
  config: Record<string, unknown>;
}

export interface PopulationPack {
  id: ArtifactId;
  owner_id: ArtifactId | null;
  name: string;
  version: string;
  description: string;
  visibility: Visibility;
  spec: Record<string, unknown>;
  status: ReleaseStatus;
  content_sha256: string | null;
  created_at: string;
  updated_at: string;
  published_at: string | null;
}

export interface PlatformRuntimeIdentity {
  git_commit: string | null;
  configured_by: "EFLUX_GIT_COMMIT" | "repository" | "unavailable";
}

export interface CreatePopulationPackInput {
  name: string;
  version: string;
  description?: string;
  visibility: Visibility;
  spec: Record<string, unknown>;
}

export async function listAgentReleases(): Promise<AgentRelease[]> {
  const { data } = await api.get<AgentRelease[]>("/agent-releases");
  return data;
}

export async function fetchPlatformRuntimeIdentity(): Promise<PlatformRuntimeIdentity> {
  const { data } = await api.get<PlatformRuntimeIdentity>(
    "/platform-runtime-identity",
  );
  return data;
}

export async function createAgentRelease(
  body: CreateAgentReleaseInput,
): Promise<AgentRelease> {
  const { data } = await api.post<AgentRelease>("/agent-releases", body);
  return data;
}

export async function fetchAgentRelease(id: ArtifactId): Promise<AgentRelease> {
  const { data } = await api.get<AgentRelease>(`/agent-releases/${id}`);
  return data;
}

export async function updateAgentRelease(
  id: ArtifactId,
  body: UpdateAgentReleaseInput,
): Promise<AgentRelease> {
  const { data } = await api.patch<AgentRelease>(`/agent-releases/${id}`, body);
  return data;
}

export async function publishAgentRelease(
  id: ArtifactId,
): Promise<AgentRelease> {
  const { data } = await api.post<AgentRelease>(
    `/agent-releases/${id}/publish`,
  );
  return data;
}

export async function forkAgentRelease(
  id: ArtifactId,
  body: ForkAgentReleaseInput,
): Promise<AgentRelease> {
  const { data } = await api.post<AgentRelease>(
    `/agent-releases/${id}/fork`,
    body,
  );
  return data;
}

export async function deployAgentRelease(
  id: ArtifactId,
  body: DeployAgentReleaseInput,
): Promise<AgentReleaseDeployment> {
  const { data } = await api.post<AgentReleaseDeployment>(
    `/agent-releases/${id}/deploy`,
    body,
  );
  return data;
}

export async function promoteAgentDeploymentLive(
  deploymentId: ArtifactId,
): Promise<AgentReleaseDeployment> {
  const { data } = await api.post<AgentReleaseDeployment>(
    `/agent-deployments/${deploymentId}/promote-live`,
    { risk_acknowledged: true },
  );
  return data;
}

export async function listReleaseEvaluations(
  id: ArtifactId,
): Promise<ReleaseEvaluation[]> {
  const { data } = await api.get<ReleaseEvaluation[]>(
    `/agent-releases/${id}/evaluations`,
  );
  return data;
}

export async function createReleaseEvaluation(
  id: ArtifactId,
  body: CreateReleaseEvaluationInput,
): Promise<ReleaseEvaluation> {
  const { data } = await api.post<ReleaseEvaluation>(
    `/agent-releases/${id}/evaluations`,
    body,
  );
  return data;
}

export async function listBehaviorDatasets(): Promise<BehaviorDataset[]> {
  const { data } = await api.get<BehaviorDataset[]>("/behavior-datasets");
  return data;
}

export async function createBehaviorDataset(
  body: CreateBehaviorDatasetInput,
): Promise<BehaviorDataset> {
  const { data } = await api.post<BehaviorDataset>("/behavior-datasets", body);
  return data;
}

export async function exportMarketSessionDataset(
  marketSessionId: ArtifactId,
  body: ExportMarketSessionDatasetInput,
): Promise<BehaviorDataset> {
  const { data } = await api.post<BehaviorDataset>(
    `/market-sessions/${marketSessionId}/behavior-datasets`,
    body,
  );
  return data;
}

export async function uploadBehaviorDatasetArtifact(
  id: ArtifactId,
  file: File,
): Promise<BehaviorDataset> {
  const format = file.name.endsWith(".gz") ? "jsonl_gz" : "jsonl";
  const { data } = await api.put<BehaviorDataset>(
    `/behavior-datasets/${id}/artifact`,
    file,
    {
      params: { artifact_format: format },
      headers: { "Content-Type": "application/octet-stream" },
    },
  );
  return data;
}

export async function fetchBehaviorDataset(
  id: ArtifactId,
): Promise<BehaviorDataset> {
  const { data } = await api.get<BehaviorDataset>(`/behavior-datasets/${id}`);
  return data;
}

export async function updateBehaviorDataset(
  id: ArtifactId,
  body: UpdateBehaviorDatasetInput,
): Promise<BehaviorDataset> {
  const { data } = await api.patch<BehaviorDataset>(
    `/behavior-datasets/${id}`,
    body,
  );
  return data;
}

export async function publishBehaviorDataset(
  id: ArtifactId,
): Promise<BehaviorDataset> {
  const { data } = await api.post<BehaviorDataset>(
    `/behavior-datasets/${id}/publish`,
  );
  return data;
}

export async function downloadBehaviorDataset(
  id: ArtifactId,
  fallbackName: string,
): Promise<void> {
  const { data, headers } = await api.get<Blob>(
    `/behavior-datasets/${id}/download`,
    { responseType: "blob" },
  );
  const disposition =
    typeof headers["content-disposition"] === "string"
      ? headers["content-disposition"]
      : "";
  const match = disposition.match(/filename\*?=(?:UTF-8''|\")?([^\";]+)/i);
  const filename = match
    ? decodeURIComponent(match[1].replace(/\"$/, ""))
    : `${fallbackName}.jsonl`;
  const url = URL.createObjectURL(data);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export async function trainBehaviorDataset(
  id: ArtifactId,
  body: CreateTrainingRunInput,
): Promise<TrainingRun> {
  const { data } = await api.post<TrainingRun>(
    `/behavior-datasets/${id}/train`,
    body,
  );
  return data;
}

export async function fetchTrainingRun(id: ArtifactId): Promise<TrainingRun> {
  const { data } = await api.get<TrainingRun>(`/training-runs/${id}`);
  return data;
}

export async function listPopulationPacks(): Promise<PopulationPack[]> {
  const { data } = await api.get<PopulationPack[]>("/population-packs");
  return data;
}

export async function createPopulationPack(
  body: CreatePopulationPackInput,
): Promise<PopulationPack> {
  const { data } = await api.post<PopulationPack>("/population-packs", body);
  return data;
}
