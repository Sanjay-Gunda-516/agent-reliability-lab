import type { UseQueryResult } from "@tanstack/react-query";
import { AlertCircle, Inbox } from "lucide-react";
import type { ReactNode } from "react";

import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

export function LoadingState({ rows = 3, label = "Loading" }: { rows?: number; label?: string }) {
  return (
    <div role="status" aria-label={label} className="flex flex-col gap-3">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-10 w-full bg-surface-muted" />
      ))}
      <span className="sr-only">{label}…</span>
    </div>
  );
}

export function EmptyState({ message, hint }: { message: string; hint?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-10 text-center">
      <span className="flex size-10 items-center justify-center rounded-full bg-blue-soft text-blue-strong">
        <Inbox aria-hidden className="size-5" />
      </span>
      <p className="text-sm font-medium text-navy">{message}</p>
      {hint && <p className="max-w-md text-xs text-text-muted">{hint}</p>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message =
    error instanceof ApiError
      ? error.isNotFound
        ? "Not found."
        : error.detail
      : "Something went wrong while loading this.";
  return (
    <div
      role="alert"
      className="flex flex-col items-center gap-3 rounded-lg border border-critical/25 bg-critical-soft px-6 py-8 text-center"
    >
      <AlertCircle aria-hidden className="size-5 text-critical-strong" />
      <p className="text-sm text-critical-strong">{message}</p>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}

interface QueryStateProps<T> {
  query: UseQueryResult<T>;
  children: (data: T) => ReactNode;
  isEmpty?: (data: T) => boolean;
  emptyMessage?: string;
  emptyHint?: ReactNode;
  loadingRows?: number;
  /** Pad the loading and error states, for use inside edge-to-edge panels. */
  padded?: boolean;
}

/** Loading, error and empty handling for one query, in one consistent style. */
export function QueryState<T>({
  query,
  children,
  isEmpty,
  emptyMessage = "Nothing here yet.",
  emptyHint,
  loadingRows,
  padded,
}: QueryStateProps<T>) {
  const pad = (node: ReactNode) => (padded ? <div className="p-5">{node}</div> : node);
  if (query.isPending) return pad(<LoadingState rows={loadingRows} />);
  if (query.isError)
    return pad(<ErrorState error={query.error} onRetry={() => void query.refetch()} />);
  if (isEmpty?.(query.data)) return <EmptyState message={emptyMessage} hint={emptyHint} />;
  return <>{children(query.data)}</>;
}
