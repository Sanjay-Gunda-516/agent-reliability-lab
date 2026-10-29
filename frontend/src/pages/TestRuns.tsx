import { useState } from "react";
import { Link, useSearchParams } from "react-router";

import { useTestRuns } from "@/api/queries";
import type { TestRunListItem } from "@/api/types";
import { FilterBar, FilterSelect } from "@/components/common/Filters";
import { PageHeader } from "@/components/common/PageHeader";
import { Panel } from "@/components/common/Panel";
import { QueryState } from "@/components/common/QueryState";
import { StatusBadge } from "@/components/common/StatusBadge";
import { VersionSelect } from "@/components/common/VersionSelect";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { checkSet, dateTime, ms, usd } from "@/lib/format";
import { runStatusStyle } from "@/lib/status";

const STATUS_OPTIONS = ["completed", "running", "queued", "failed"].map((s) => ({
  value: s,
  label: runStatusStyle(s).label,
}));

/** Variants with at least one critical failure: graded variants minus policy-compliant ones. */
function criticalVariants(run: TestRunListItem): number | null {
  const m = run.metrics;
  return m ? m.policy_compliance.total - m.policy_compliance.passed : null;
}

function failedChecks(run: TestRunListItem): number | null {
  return run.metrics ? Object.values(run.metrics.failed_checks).reduce((a, b) => a + b, 0) : null;
}

function RunsTable({ runs }: { runs: TestRunListItem[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Run</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Agent version</TableHead>
          <TableHead className="text-right">Variants</TableHead>
          <TableHead>Pass / fail / crash</TableHead>
          <TableHead className="text-right">Critical variants</TableHead>
          <TableHead className="text-right">Failed checks</TableHead>
          <TableHead className="text-right">Mean latency</TableHead>
          <TableHead className="text-right">Est. cost</TableHead>
          <TableHead>Check set</TableHead>
          <TableHead>Created</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {runs.map((run) => {
          const m = run.metrics;
          const critical = criticalVariants(run);
          return (
            <TableRow key={run.id}>
              <TableCell>
                <Link to={`/runs/${run.id}`} className="font-medium text-blue-strong hover:underline">
                  #{run.id}
                </Link>
                {run.rerun_id !== null && (
                  <span className="ml-2 text-xs text-text-muted">
                    rerun {run.rerun_id} · {run.rerun_side} {run.repetition}
                  </span>
                )}
              </TableCell>
              <TableCell>
                <StatusBadge style={runStatusStyle(run.status)} />
              </TableCell>
              <TableCell className="text-navy">{run.agent_version}</TableCell>
              <TableCell className="text-right tabular-nums">{run.variant_count}</TableCell>
              <TableCell className="tabular-nums">
                {m ? (
                  <span>
                    <span className="text-success-strong">{m.passed}</span>
                    <span className="text-text-muted"> / </span>
                    <span className={m.failed ? "text-warning-strong" : ""}>{m.failed}</span>
                    <span className="text-text-muted"> / </span>
                    <span className={m.errored ? "text-critical-strong" : ""}>{m.errored}</span>
                  </span>
                ) : (
                  "—"
                )}
              </TableCell>
              <TableCell
                className={`text-right tabular-nums ${critical ? "font-medium text-critical-strong" : ""}`}
              >
                {critical ?? "—"}
              </TableCell>
              <TableCell className="text-right tabular-nums">{failedChecks(run) ?? "—"}</TableCell>
              <TableCell className="text-right tabular-nums">{ms(m?.latency.mean_ms)}</TableCell>
              <TableCell className="text-right tabular-nums">{usd(m?.estimated_cost_usd)}</TableCell>
              <TableCell>
                <span className={run.check_set_version ? "text-navy" : "text-text-muted"}>
                  {checkSet(run.check_set_version)}
                </span>
              </TableCell>
              <TableCell className="text-text-muted">{dateTime(run.created_at)}</TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

export function TestRuns() {
  const [params, setParams] = useSearchParams();
  const version = params.get("version") ?? "";
  const status = params.get("status") ?? "";
  const [showReruns, setShowReruns] = useState(false);
  const runs = useTestRuns({
    agent_version: version || undefined,
    status: status || undefined,
    include_reruns: showReruns,
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
        title="Test runs"
        description="Each run grades a selection of scenario variants against one agent version."
      />
      <FilterBar>
        <VersionSelect value={version} onChange={(v) => setParam("version", v)} />
        <FilterSelect
          label="Status"
          value={status}
          onChange={(v) => setParam("status", v)}
          options={STATUS_OPTIONS}
          allLabel="All statuses"
        />
        <label className="flex items-center gap-2 text-sm text-text-muted">
          <input
            type="checkbox"
            className="size-4 accent-[var(--blue)]"
            checked={showReruns}
            onChange={(e) => setShowReruns(e.target.checked)}
          />
          Include regression reruns
        </label>
      </FilterBar>
      <Panel flush>
        <QueryState
          query={runs}
          isEmpty={(d) => d.length === 0}
          emptyMessage={version || status ? "No test runs match these filters." : "No test runs yet."}
          loadingRows={5}
          padded
        >
          {(data) => <RunsTable runs={data} />}
        </QueryState>
      </Panel>
    </>
  );
}
