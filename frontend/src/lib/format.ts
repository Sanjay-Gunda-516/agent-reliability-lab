// Display formatting. Every function returns "—" for missing values so tables never
// show "null", "NaN" or "undefined".

const DASH = "—";

export function percent(rate: number | null | undefined, digits = 0): string {
  if (rate === null || rate === undefined) return DASH;
  return `${(rate * 100).toFixed(digits)}%`;
}

export function fraction(passed: number, total: number): string {
  return total ? `${passed}/${total}` : DASH;
}

export function ms(value: number | null | undefined): string {
  if (value === null || value === undefined) return DASH;
  if (value >= 1000) return `${(value / 1000).toFixed(value >= 10_000 ? 1 : 2)} s`;
  return `${Math.round(value)} ms`;
}

export function usd(value: string | number | null | undefined, digits = 4): string {
  if (value === null || value === undefined || value === "") return DASH;
  const amount = typeof value === "number" ? value : Number(value);
  return Number.isFinite(amount) ? `$${amount.toFixed(digits)}` : DASH;
}

export function count(value: number | null | undefined): string {
  if (value === null || value === undefined) return DASH;
  return value.toLocaleString("en-US");
}

export function compact(value: number | null | undefined): string {
  if (value === null || value === undefined) return DASH;
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(
    value,
  );
}

export function signed(value: number | null | undefined, unit = "", digits = 2): string {
  if (value === null || value === undefined) return DASH;
  const rounded = Number(value.toFixed(digits));
  const sign = rounded > 0 ? "+" : rounded < 0 ? "−" : "±";
  return `${sign}${Math.abs(rounded).toLocaleString("en-US", { maximumFractionDigits: digits })}${unit}`;
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return DASH;
  return new Date(iso).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return DASH;
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export function shortHash(hash: string | null | undefined): string {
  return hash ? hash.slice(0, 8) : DASH;
}

export function shortId(id: string | null | undefined): string {
  return id ? id.slice(0, 8) : DASH;
}

/** "tools.lookup_before_action" -> "Tools · lookup before action" style labels. */
export function humanize(value: string | null | undefined): string {
  if (!value) return DASH;
  const text = value.replace(/[_.]/g, " ").replace(/\s+/g, " ").trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function checkSet(version: string | null | undefined): string {
  return version ?? "unknown";
}
