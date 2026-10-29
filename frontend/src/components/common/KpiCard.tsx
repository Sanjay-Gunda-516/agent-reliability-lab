import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

interface KpiCardProps {
  label: string;
  value: ReactNode;
  icon?: LucideIcon;
  /** e.g. "18/20 variants". */
  detail?: ReactNode;
  /** A change against an earlier run; only pass one that comes from real data. */
  trend?: { text: string; good: boolean | null; title: string };
  accent?: "blue" | "tan";
}

export function KpiCard({ label, value, icon: Icon, detail, trend, accent = "blue" }: KpiCardProps) {
  return (
    <div className="flex min-w-0 flex-col gap-2 rounded-xl border border-border bg-surface p-4 shadow-card">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-xs font-medium text-text-muted">{label}</span>
        {Icon && (
          <span
            className={cn(
              "flex size-7 items-center justify-center rounded-lg",
              accent === "tan" ? "bg-tan-soft text-tan-strong" : "bg-blue-soft text-blue-strong",
            )}
          >
            <Icon aria-hidden className="size-4" />
          </span>
        )}
      </div>
      <div className="text-2xl font-semibold tracking-tight text-navy tabular-nums">{value}</div>
      <div className="flex min-h-4 flex-wrap items-center gap-x-2 text-xs text-text-muted">
        {detail && <span>{detail}</span>}
        {trend && (
          <span
            title={trend.title}
            className={cn(
              "font-medium tabular-nums",
              trend.good === null
                ? "text-neutral-strong"
                : trend.good
                  ? "text-success-strong"
                  : "text-critical-strong",
            )}
          >
            {trend.text}
          </span>
        )}
      </div>
    </div>
  );
}
