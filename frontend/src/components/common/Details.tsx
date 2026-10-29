import { ChevronRight } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/** A labelled list of facts, two columns on wide screens. */
export function KeyValues({
  items,
  className,
}: {
  items: { label: string; value: ReactNode; mono?: boolean }[];
  className?: string;
}) {
  return (
    <dl className={cn("grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2", className)}>
      {items.map(({ label, value, mono }) => (
        <div key={label} className="min-w-0">
          <dt className="text-xs text-text-muted">{label}</dt>
          <dd className={cn("mt-0.5 truncate text-sm text-navy", mono && "font-mono text-xs")}>
            {value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function Tags({ tags }: { tags: string[] }) {
  return (
    <span className="flex flex-wrap gap-1">
      {tags.map((tag) => (
        <span
          key={tag}
          className="rounded-md border border-border bg-surface-muted px-1.5 py-0.5 text-xs text-text-muted"
        >
          {tag}
        </span>
      ))}
    </span>
  );
}

/** Raw data, collapsed until someone asks for it. */
export function RawDetails({ label = "Raw details", value }: { label?: string; value: unknown }) {
  return (
    <details className="group rounded-lg border border-border bg-surface-muted/60">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 px-3 py-2 text-xs font-medium text-text-muted select-none hover:text-navy [&::-webkit-details-marker]:hidden">
        <ChevronRight
          aria-hidden
          className="size-3.5 transition-transform duration-200 group-open:rotate-90"
        />
        {label}
      </summary>
      <pre className="max-h-96 overflow-auto border-t border-border px-3 py-2 font-mono text-xs leading-relaxed text-navy">
        {JSON.stringify(value, null, 2)}
      </pre>
    </details>
  );
}

/** A value from the API rendered readably: scalars inline, small objects as key: value. */
export function Value({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span className="text-text-muted">—</span>;
  if (typeof value === "boolean") return <>{value ? "yes" : "no"}</>;
  if (typeof value !== "object") return <>{String(value)}</>;
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-text-muted">none</span>;
    return (
      <ul className="flex flex-col gap-0.5">
        {value.map((item, i) => (
          <li key={i}>
            <Value value={item} />
          </li>
        ))}
      </ul>
    );
  }
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length === 0) return <span className="text-text-muted">none</span>;
  return (
    <span className="flex flex-wrap gap-x-3 gap-y-0.5">
      {entries.map(([k, v]) => (
        <span key={k}>
          <span className="text-text-muted">{k}:</span> {inline(v)}
        </span>
      ))}
    </span>
  );
}

/** One-line text for a nested value: {"4821": "cancelled"} -> "4821 cancelled". */
function inline(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value !== "object") return String(value);
  if (Array.isArray(value)) return value.length ? value.map(inline).join(", ") : "none";
  const entries = Object.entries(value as Record<string, unknown>);
  return entries.length ? entries.map(([k, v]) => `${k} ${inline(v)}`).join(", ") : "none";
}

export function Mono({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <span title={title} className="font-mono text-xs text-navy">
      {children}
    </span>
  );
}
