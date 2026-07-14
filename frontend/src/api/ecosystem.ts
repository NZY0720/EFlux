import { api } from "./client";
import type { schemas } from "./schema.gen";

export type PathId = number;
export type AgentRelease = schemas["AgentReleaseOut"];
export type CreateAgentReleaseInput = schemas["AgentReleaseCreateIn"];
export type UpdateAgentReleaseInput = schemas["AgentReleasePatchIn"];
export type ForkAgentReleaseInput = schemas["AgentReleaseForkIn"];
export type DeployAgentReleaseInput = schemas["AgentReleaseDeployIn"];
export type AgentReleaseDeployment = schemas["AgentReleaseDeploymentOut"];
export type ReleaseEvaluation = schemas["ReleaseEvaluationOut"];
export type CreateReleaseEvaluationInput =
  schemas["ReleaseEvaluationCreateIn"];
export type BehaviorDataset = schemas["BehaviorDatasetOut"];
export type CreateBehaviorDatasetInput = schemas["BehaviorDatasetCreateIn"];
export type ExportMarketSessionDatasetInput =
  schemas["BehaviorDatasetExportIn"];
export type UpdateBehaviorDatasetInput = schemas["BehaviorDatasetPatchIn"];
export type TrainingRun = schemas["DatasetTrainingRunOut"];
export type CreateTrainingRunInput = schemas["DatasetTrainIn"];
export type PopulationPack = schemas["PopulationPackOut"];
export type CreatePopulationPackInput = schemas["PopulationPackCreateIn"];
export type PlatformRuntimeIdentity = schemas["PlatformRuntimeIdentityOut"];

export type Market = AgentRelease["market"];
export type Visibility = AgentRelease["visibility"];
export type ReleaseStatus = AgentRelease["status"];
export type Provenance = ReleaseEvaluation["provenance"];
export type EvaluationKind = ReleaseEvaluation["kind"];
export type EvaluationStatus = ReleaseEvaluation["status"];
export type TrainingStatus = TrainingRun["status"];
export type TrainingAlgorithm = TrainingRun["algorithm"];

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

export async function fetchAgentRelease(
  id: PathId,
  signal?: AbortSignal,
): Promise<AgentRelease> {
  const { data } = await api.get<AgentRelease>(`/agent-releases/${id}`, {
    signal,
  });
  return data;
}

export async function updateAgentRelease(
  id: PathId,
  body: UpdateAgentReleaseInput,
): Promise<AgentRelease> {
  const { data } = await api.patch<AgentRelease>(`/agent-releases/${id}`, body);
  return data;
}

export async function publishAgentRelease(
  id: PathId,
): Promise<AgentRelease> {
  const { data } = await api.post<AgentRelease>(
    `/agent-releases/${id}/publish`,
  );
  return data;
}

export async function forkAgentRelease(
  id: PathId,
  body: ForkAgentReleaseInput,
): Promise<AgentRelease> {
  const { data } = await api.post<AgentRelease>(
    `/agent-releases/${id}/fork`,
    body,
  );
  return data;
}

export async function deployAgentRelease(
  id: PathId,
  body: DeployAgentReleaseInput,
): Promise<AgentReleaseDeployment> {
  const { data } = await api.post<AgentReleaseDeployment>(
    `/agent-releases/${id}/deploy`,
    body,
  );
  return data;
}

export async function promoteAgentDeploymentLive(
  deploymentId: PathId,
): Promise<AgentReleaseDeployment> {
  const { data } = await api.post<AgentReleaseDeployment>(
    `/agent-deployments/${deploymentId}/promote-live`,
    { risk_acknowledged: true },
  );
  return data;
}

export async function listReleaseEvaluations(
  id: PathId,
  signal?: AbortSignal,
): Promise<ReleaseEvaluation[]> {
  const { data } = await api.get<ReleaseEvaluation[]>(
    `/agent-releases/${id}/evaluations`,
    { signal },
  );
  return data;
}

export async function createReleaseEvaluation(
  id: PathId,
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
  marketSessionId: PathId,
  body: ExportMarketSessionDatasetInput,
): Promise<BehaviorDataset> {
  const { data } = await api.post<BehaviorDataset>(
    `/market-sessions/${marketSessionId}/behavior-datasets`,
    body,
  );
  return data;
}

export async function uploadBehaviorDatasetArtifact(
  id: PathId,
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
  id: PathId,
): Promise<BehaviorDataset> {
  const { data } = await api.get<BehaviorDataset>(`/behavior-datasets/${id}`);
  return data;
}

export async function updateBehaviorDataset(
  id: PathId,
  body: UpdateBehaviorDatasetInput,
): Promise<BehaviorDataset> {
  const { data } = await api.patch<BehaviorDataset>(
    `/behavior-datasets/${id}`,
    body,
  );
  return data;
}

export async function publishBehaviorDataset(
  id: PathId,
): Promise<BehaviorDataset> {
  const { data } = await api.post<BehaviorDataset>(
    `/behavior-datasets/${id}/publish`,
  );
  return data;
}

export async function downloadBehaviorDataset(
  id: PathId,
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
  id: PathId,
  body: CreateTrainingRunInput,
): Promise<TrainingRun> {
  const { data } = await api.post<TrainingRun>(
    `/behavior-datasets/${id}/train`,
    body,
  );
  return data;
}

export async function fetchTrainingRun(id: PathId): Promise<TrainingRun> {
  const { data } = await api.get<TrainingRun>(`/training-runs/${id}`);
  return data;
}

export async function listPopulationPacks(
  signal?: AbortSignal,
): Promise<PopulationPack[]> {
  const { data } = await api.get<PopulationPack[]>("/population-packs", {
    signal,
  });
  return data;
}

export async function createPopulationPack(
  body: CreatePopulationPackInput,
): Promise<PopulationPack> {
  const { data } = await api.post<PopulationPack>("/population-packs", body);
  return data;
}
