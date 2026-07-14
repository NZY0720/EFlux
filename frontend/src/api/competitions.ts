import { api, listCompetitions } from "./client";

export interface CompetitionTarget {
  slug: string;
  title: string;
}

const FALLBACK_COMPETITION: CompetitionTarget = { slug: "season-0", title: "Season 0" };

export async function resolveActiveCompetition(): Promise<CompetitionTarget> {
  try {
    const competitions = await listCompetitions();
    const active = competitions.find((item) => item.status === "open")
      ?? competitions.find((item) => item.status === "active")
      ?? competitions[0];
    return active ? { slug: active.slug, title: active.title } : FALLBACK_COMPETITION;
  } catch {
    return FALLBACK_COMPETITION;
  }
}

export interface ManagedSubmissionPayload {
  algorithm: string;
  llm_enabled: false;
  preset?: string;
  endowment?: Record<string, unknown>;
  risk?: unknown;
}

export interface SubmissionCreateInput {
  track: "managed";
  payload: ManagedSubmissionPayload;
}

export interface Submission {
  id: number;
  competition_id: number;
  track: string;
  status: string;
  payload: Record<string, unknown>;
  selected_for_final: boolean;
  selected_for_final_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface EvaluationSeedRun {
  seed_label: string;
  attempt: number;
  status: string;
  score: number | null;
}

export interface EvaluationRun {
  id: number;
  kind: "hidden" | "holdout" | string;
  status: string;
  rules_version: string;
  score: number | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  seed_runs: EvaluationSeedRun[];
}

export interface SubmissionDetail extends Submission {
  latest_run: EvaluationRun | null;
  evaluation_runs: EvaluationRun[];
}

export type VppPresetConfig = Record<string, string | number | boolean>;

export async function listVppPresets(): Promise<Record<string, VppPresetConfig>> {
  const { data } = await api.get<Record<string, VppPresetConfig>>("/vpps/presets");
  return data;
}

export async function createCompetitionSubmission(
  slug: string,
  body: SubmissionCreateInput,
): Promise<Submission> {
  const { data } = await api.post<Submission>(`/competitions/${slug}/submissions`, body);
  return data;
}

export async function fetchSubmission(id: number): Promise<SubmissionDetail> {
  const { data } = await api.get<SubmissionDetail>(`/submissions/${id}`);
  return data;
}

export async function enqueueSubmissionEvaluation(id: number): Promise<EvaluationRun> {
  const { data } = await api.post<EvaluationRun>(`/submissions/${id}/evaluate`);
  return data;
}

export async function selectFinalSubmission(id: number): Promise<void> {
  await api.post(`/submissions/${id}/select-final`);
}

export async function downloadEvaluationEvidence(id: number): Promise<void> {
  const { data } = await api.get<Blob>(`/evaluation-runs/${id}/evidence`, {
    responseType: "blob",
  });
  const url = URL.createObjectURL(data);
  const link = document.createElement("a");
  link.href = url;
  link.download = `evaluation-${id}-evidence.json`;
  link.click();
  URL.revokeObjectURL(url);
}
