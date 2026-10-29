import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

interface PanelProps {
  title?: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  /** Tables run edge to edge inside the panel. */
  flush?: boolean;
}

/** The white, bordered card every section of a page sits in. */
export function Panel({ title, description, action, children, className, flush }: PanelProps) {
  return (
    <section
      className={cn("min-w-0 rounded-xl border border-border bg-surface shadow-card", className)}
    >
      {(title || action) && (
        <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
          <div className="min-w-0">
            {title && <h2 className="text-sm font-semibold text-navy">{title}</h2>}
            {description && <p className="mt-0.5 text-xs text-text-muted">{description}</p>}
          </div>
          {action}
        </div>
      )}
      <div className={flush ? "" : "p-5"}>{children}</div>
    </section>
  );
}
