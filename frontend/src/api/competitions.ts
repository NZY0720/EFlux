import { api } from "./client";

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
