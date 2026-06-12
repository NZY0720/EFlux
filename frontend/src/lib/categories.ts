// Shared palette + labels for agent categories (merit-order buckets).
// Order matters: it mirrors the merit order, cheapest energy first.

export interface CategoryMeta {
  label: string;
  color: string;
}

export const CATEGORY_ORDER = ["solar", "wind", "battery_load", "llm", "gas", "external"] as const;

export const CATEGORY_META: Record<string, CategoryMeta> = {
  solar: { label: "Solar", color: "#f59e0b" },
  wind: { label: "Wind", color: "#38bdf8" },
  battery_load: { label: "Battery / load", color: "#a78bfa" },
  llm: { label: "LLM agent", color: "#10b981" },
  gas: { label: "Gas", color: "#f43f5e" },
  external: { label: "External", color: "#94a3b8" },
};

export function categoryMeta(category: string): CategoryMeta {
  return CATEGORY_META[category] ?? { label: category, color: "#64748b" };
}

/** Friendly name for the backend's agent strategy strings. */
export function strategyLabel(strategy: string): string {
  if (strategy.startsWith("ZIAgent")) return "Zero-Intelligence (random)";
  if (strategy.startsWith("TruthfulAgent")) return "Truthful (cost-based)";
  if (strategy.startsWith("GasGeneratorAgent")) return "Gas (marginal cost)";
  if (strategy.startsWith("ReflectiveAgent")) return "LLM Reflective";
  if (strategy.startsWith("PPOAgent")) return "PPO (trained policy)";
  return strategy;
}
