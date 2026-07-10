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

/** Market prices always display at two decimal places, except values that round to zero. */
export function formatPrice(value: number | string | null | undefined): string {
  const n = typeof value === "string" ? Number(value) : value;
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  return Math.abs(n) < 0.005 ? "0" : n.toFixed(2);
}

/** Trade quantities retain the established 3dp precision without negative-zero noise. */
export function formatQuantity(value: number | string | null | undefined): string {
  const n = typeof value === "string" ? Number(value) : value;
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  return Math.abs(n) < 0.0005 ? "0" : n.toFixed(3);
}

function trimOneDecimal(value: number): string {
  return value.toFixed(1).replace(/\.0$/, "");
}
