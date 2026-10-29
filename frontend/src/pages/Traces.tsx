import { useState } from "react";
import { Link, useSearchParams } from "react-router";

import { useTraces } from "@/api/queries";
import type { TraceSummary } from "@/api/types";
import { Mono } from "@/components/common/Details";
import { FilterBar, FilterSelect, SearchInput } from "@/components/common/Filters";
import { PageHeader } from "@/components/common/PageHeader";
import { Panel } from "@/components/common/Panel";
import { EmptyState, QueryState } from "@/components/common/QueryState";
import { StatusBadge } from "@/components/common/StatusBadge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { dateTime, ms, shortId } from "@/lib/format";
import { executionStyle } from "@/lib/status";

const SOURCES = [
  { value: "api", label: "Live chat (API)" },
  { value: "evaluation", label: "Evaluation" },
];
const STATUSES = ["completed", "escalated", "pending_approval", "error"].map((s) => ({
  value: s,
  label: executionStyle(s).label,
}));

function duration(t: TraceSummary): number | null {
  return t.finished_at ? new Date(t.finished_at).getTime() - new Date(t.started_at).getTime() : null;
}

function TraceTable({ traces }: { traces: TraceSummary[] }) {
  const [search, setSearch] = useState("");
  const q = search.trim().toLowerCase();
  const shown = traces.filter(
    (t) =>
      !q ||
      t.execution_id.includes(q) ||
      (t.request_id ?? "").includes(q) ||
      (t.variant_id ?? "").toLowerCase().includes(q),
  );
  return (
    <>
      <div className="border-b border-border px-5 py-3">
        <SearchInput
          value={search}
          onChange={setSearch}
          label="Search traces"
          placeholder="Execution id, request id or variant"
        />
      </div>
      {shown.length === 0 ? (
        <EmptyState message="No traces match this search." />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Execution</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Source</TableHead>
              <TableHead>What</TableHead>
              <TableHead>Agent version</TableHead>
              <TableHead className="text-right">Duration</TableHead>
              <TableHead>Started</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {shown.map((t) => (
              <TableRow key={t.execution_id}>
                <TableCell>
                  <Link to={`/traces/${t.execution_id}`} className="font-mono text-xs font-medium text-blue-strong hover:underline">
                    {shortId(t.execution_id)}
                  </Link>
                </TableCell>
                <TableCell>
                  <StatusBadge style={executionStyle(t.status)} />
                </TableCell>
                <TableCell className="text-text-muted">{t.source === "api" ? "Live chat" : "Evaluation"}</TableCell>
                <TableCell className="text-navy">
                  {t.variant_id ? (
                    <>
                      {t.variant_id}
                      {t.test_run_id !== null && <span className="ml-1.5 text-xs text-text-muted">run #{t.test_run_id}</span>}
                    </>
                  ) : (
                    <>customer {t.customer_id}</>
                  )}
                </TableCell>
                <TableCell className="text-text-muted">{t.agent_version ?? "—"}</TableCell>
                <TableCell className="text-right tabular-nums">{ms(duration(t))}</TableCell>
                <TableCell className="text-text-muted">{dateTime(t.started_at)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </>
  );
}

export function Traces() {
  const [params, setParams] = useSearchParams();
  const source = params.get("source") ?? "";
  const status = params.get("status") ?? "";
  const runId = params.get("run");
  const traces = useTraces({
    source: source || undefined,
    status: status || undefined,
    test_run_id: runId ? Number(runId) : undefined,
  });
  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };
  return (
    <>
      <PageHeader
        title="Traces"
        description="Every agent execution, live or in an evaluation, with its full timeline. Newest first."
      />
      <FilterBar>
        <FilterSelect label="Source" value={source} onChange={(v) => setParam("source", v)} options={SOURCES} allLabel="All sources" />
        <FilterSelect label="Status" value={status} onChange={(v) => setParam("status", v)} options={STATUSES} allLabel="All statuses" />
        {runId && (
          <button type="button" onClick={() => setParam("run", "")} className="rounded-md border border-border bg-surface px-2 py-1 text-xs text-navy hover:bg-surface-muted">
            Run #{runId} ✕
          </button>
        )}
      </FilterBar>
      <Panel flush>
        <QueryState
          query={traces}
          padded
          isEmpty={(d) => d.length === 0}
          emptyMessage={source || status || runId ? "No traces match these filters." : "No traces yet."}
        >
          {(data) => <TraceTable traces={data} />}
        </QueryState>
      </Panel>
      <p className="mt-3 text-xs text-text-muted">
        Showing up to 100 traces. Ids are shortened; hover or open a trace for the full <Mono>execution_id</Mono>.
      </p>
    </>
  );
}
