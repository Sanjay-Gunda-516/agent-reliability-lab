import { useState } from "react";
import { Link } from "react-router";

import { useAgentVersions, useComparisons, useTestRuns } from "@/api/queries";
import type { AgentVersion, ReportSummary, TestRunListItem } from "@/api/types";
import { Mono } from "@/components/common/Details";
import { FilterSelect } from "@/components/common/Filters";
import { PageHeader } from "@/components/common/PageHeader";
import { Panel } from "@/components/common/Panel";
import { QueryState } from "@/components/common/QueryState";
import { StatusBadge } from "@/components/common/StatusBadge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { dateTime, fraction, humanize, shortHash } from "@/lib/format";
import { gateStyle } from "@/lib/status";
import { cn } from "@/lib/utils";

function VersionCard({ v, runs, reports }: { v: AgentVersion; runs: TestRunListItem[]; reports: ReportSummary[] }) {
  const own = runs.filter((r) => r.agent_version === v.name && r.rerun_id === null);
  const related = reports.filter((r) => r.baseline_agent_version === v.name || r.candidate_agent_version === v.name);
  return (
    <li className="flex flex-col gap-4 rounded-xl border border-border bg-surface p-5 shadow-card">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold text-navy">{v.name}</h2>
          <p className="text-sm text-text-muted">{v.description}</p>
        </div>
        <span
          className={cn(
            "rounded-md border px-2 py-0.5 text-xs font-medium",
            v.defined_in_code ? "border-blue/25 bg-blue-soft text-navy" : "border-neutral/30 bg-neutral-soft text-neutral-strong",
          )}
        >
          {v.defined_in_code ? "Defined in code" : "Known only from old runs"}
        </span>
      </div>
      <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        <div><dt className="text-xs text-text-muted">Model</dt><dd className="text-navy">{v.model ?? "—"}</dd></div>
        <div><dt className="text-xs text-text-muted">Prompt</dt><dd className="text-navy">{v.prompt_version ?? "—"}</dd></div>
        <div><dt className="text-xs text-text-muted">Config hash</dt><dd><Mono title={v.config_hash}>{shortHash(v.config_hash)}</Mono></dd></div>
        <div><dt className="text-xs text-text-muted">First recorded</dt><dd className="text-navy">{dateTime(v.created_at)}</dd></div>
      </dl>
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <div className="mb-1 text-xs font-medium text-text-muted">Test runs ({own.length})</div>
          {own.length === 0 ? (
            <p className="text-sm text-text-muted">No runs yet.</p>
          ) : (
            <ul className="flex flex-col gap-1 text-sm">
              {own.slice(0, 4).map((r) => (
                <li key={r.id}>
                  <Link to={`/runs/${r.id}`} className="text-blue-strong hover:underline">Run #{r.id}</Link>
                  <span className="text-text-muted"> · {r.metrics ? `${fraction(r.metrics.passed, r.metrics.variants)} passed` : r.status} · {dateTime(r.created_at)}</span>
                </li>
              ))}
              {own.length > 4 && (
                <li><Link to={`/runs?version=${encodeURIComponent(v.name)}`} className="text-xs text-blue-strong hover:underline">All {own.length} runs →</Link></li>
              )}
            </ul>
          )}
        </div>
        <div>
          <div className="mb-1 text-xs font-medium text-text-muted">Comparisons ({related.length})</div>
          {related.length === 0 ? (
            <p className="text-sm text-text-muted">Not compared yet.</p>
          ) : (
            <ul className="flex flex-col gap-1.5 text-sm">
              {related.slice(0, 4).map((r) => (
                <li key={r.id} className="flex items-center gap-2 whitespace-nowrap">
                  <StatusBadge style={gateStyle(r.gate_status)} />
                  <Link to={`/regression/${r.id}`} className="text-blue-strong hover:underline">Report #{r.id}</Link>
                  <span className="truncate text-text-muted">{r.candidate_agent_version === v.name ? "as candidate" : "as baseline"}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </li>
  );
}

function format(value: unknown): string {
  if (value === undefined) return "—";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function ConfigComparison({ versions }: { versions: AgentVersion[] }) {
  const options = versions.map((v) => ({ value: v.name, label: v.name }));
  const [left, setLeft] = useState(versions[0]?.name ?? "");
  const [right, setRight] = useState(versions[1]?.name ?? versions[0]?.name ?? "");
  const a = versions.find((v) => v.name === left);
  const b = versions.find((v) => v.name === right);
  const all = [...new Set([...Object.keys(a?.config ?? {}), ...Object.keys(b?.config ?? {})])].sort();
  const differing = all.filter((k) => format(a?.config[k]) !== format(b?.config[k]));
  const keys = [...differing, ...all.filter((k) => !differing.includes(k))];
  return (
    <Panel
      title="Compare configurations"
      description={a && b ? `${differing.length} setting${differing.length === 1 ? "" : "s"} differ` : undefined}
      action={
        <div className="flex flex-wrap gap-2">
          <FilterSelect label="First version" value={left} onChange={setLeft} options={options} allLabel="Choose" />
          <FilterSelect label="Second version" value={right} onChange={setRight} options={options} allLabel="Choose" />
        </div>
      }
      flush
    >
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Setting</TableHead>
            <TableHead>{left || "—"}</TableHead>
            <TableHead>{right || "—"}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {keys.map((k) => {
            const differs = differing.includes(k);
            return (
              <TableRow key={k} className={differs ? "bg-tan-soft/60 hover:bg-tan-soft" : undefined}>
                <TableCell className="text-navy">
                  {humanize(k)}
                  {differs && <span className="ml-2 text-xs font-medium text-tan-strong">differs</span>}
                </TableCell>
                <TableCell className="font-mono text-xs">{format(a?.config[k])}</TableCell>
                <TableCell className="font-mono text-xs">{format(b?.config[k])}</TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </Panel>
  );
}

export function AgentVersions() {
  const versions = useAgentVersions();
  const runs = useTestRuns({ include_reruns: false });
  const reports = useComparisons();
  return (
    <>
      <PageHeader
        title="Agent versions"
        description="A version is a fixed configuration and prompt. Changing either means a new version name, so a run always says exactly what it tested."
      />
      <QueryState query={versions} isEmpty={(d) => d.length === 0} emptyMessage="No agent versions recorded yet.">
        {(data) => (
          <div className="flex flex-col gap-6">
            <ul className="grid gap-4 xl:grid-cols-2">
              {[...data].sort((x, y) => Number(y.defined_in_code) - Number(x.defined_in_code) || x.name.localeCompare(y.name)).map((v) => (
                <VersionCard key={v.name} v={v} runs={runs.data ?? []} reports={reports.data ?? []} />
              ))}
            </ul>
            {data.length > 1 && <ConfigComparison versions={data.filter((v) => v.defined_in_code).length > 1 ? data.filter((v) => v.defined_in_code) : data} />}
          </div>
        )}
      </QueryState>
    </>
  );
}
