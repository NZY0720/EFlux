export function formatCompactCount(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "0";
  const n = Math.max(0, Math.trunc(value));
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${trimOneDecimal(n / 1000)}k`;
  if (n < 1_000_000_000) return `${trimOneDecimal(n / 1_000_000)}m`;
  return `${trimOneDecimal(n / 1_000_000_000)}b`;
}

export function formatCompactSigned(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "0";
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return `${sign}${formatCompactCount(Math.abs(value))}`;
}

function trimOneDecimal(value: number): string {
  return value.toFixed(1).replace(/\.0$/, "");
}
