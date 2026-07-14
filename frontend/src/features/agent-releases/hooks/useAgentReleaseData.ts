import { useEffect, useMemo, useState } from "react";

import {
  fetchAgentRelease,
  listPopulationPacks,
  listReleaseEvaluations,
  type AgentRelease,
  type PopulationPack,
  type ReleaseEvaluation,
} from "../../../api/ecosystem";

export function useAgentReleaseData(releaseId: number) {
  const [release, setRelease] = useState<AgentRelease | null>(null);
  const [evaluations, setEvaluations] = useState<ReleaseEvaluation[]>([]);
  const [populationPacks, setPopulationPacks] = useState<PopulationPack[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const validId = Number.isInteger(releaseId) && releaseId > 0;

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setRelease(null);
    setEvaluations([]);
    setPopulationPacks([]);
    if (!validId) {
      setLoadError("The release ID is invalid.");
      setLoading(false);
      return () => controller.abort();
    }
    void Promise.all([
      fetchAgentRelease(releaseId, controller.signal),
      listReleaseEvaluations(releaseId, controller.signal),
      listPopulationPacks(controller.signal),
    ])
      .then(([nextRelease, nextEvaluations, nextPacks]) => {
        setRelease(nextRelease);
        setEvaluations(nextEvaluations);
        setPopulationPacks(nextPacks);
        setLoadError(null);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted)
          setLoadError(
            (error as Error).message || "Unable to load this release.",
          );
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [releaseId, validId]);

  const hasPendingEvaluation = useMemo(
    () =>
      evaluations.some(
        (evaluation) =>
          evaluation.status === "queued" || evaluation.status === "running",
      ),
    [evaluations],
  );
  useEffect(() => {
    if (!validId || !hasPendingEvaluation) return;
    let active = true;
    const refresh = () => {
      void listReleaseEvaluations(releaseId)
        .then((rows) => {
          if (active) setEvaluations(rows);
        })
        .catch(() => undefined);
    };
    const timer = window.setInterval(refresh, 3_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [hasPendingEvaluation, releaseId, validId]);

  return {
    release,
    setRelease,
    evaluations,
    setEvaluations,
    populationPacks,
    setPopulationPacks,
    loading,
    loadError,
  };
}
