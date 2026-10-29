import { ArrowLeft } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router";

import { useRunFailedChecks, useRunResults, useScenarios, useTestRun } from "@/api/queries";
import type { FailedCheck, ResultSummary, RunMetrics, ScenarioSummary } from "@/api/types";
import { KeyValues, RawDetails, Tags, Value } from "@/components/common/Details";
import { FilterBar, FilterSelect, SearchInput } from "@/components/common/Filters";
import { KpiCard } from "@/components/common/KpiCard";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { checkSet, dateTime, fraction, humanize, ms, percent, usd } from "@/lib/format";
import { runStatusStyle, severityStyle, verdictStyle } from "@/lib/status";

const VERDICTS = [
  { value: "pass", label: "Pass" },
  { value: "fail", label: "Fail" },
  { value: "error", label: "Error" },
];

function FailureCounts({ r }: { r: ResultSummary }) {
  const parts = [
    { n: r.critical_failures, cls: "text-critical-strong", label: "critical" },
    { n: r.major_failures, cls: "text-warning-strong", label: "major" },
    { n: r.minor_failures, cls: "text-neutral-strong", label: "minor" },
  ].filter((p) => p.n > 0);
  if (!parts.length) return <span className="text-text-muted">none</span>;
  return (
    <span className="flex gap-2">
      {parts.map((p) => (
        <span key={p.label} className={`${p.cls} tabular-nums`}>
          {p.n} {p.label}
        </span>
      ))}
    </span>
  );
}

export function ResultsTable({
  runId,
  results,
  scenarios,
}: {
  runId: number;
  results: ResultSummary[];
  scenarios: Map<string, ScenarioSummary>;
}) {
  const [search, setSearch] = useState("");
  const [verdict, setVerdict] = useState("");
  const shown = results.filter(
    (r) =>
      (!verdict || r.verdict === verdict) &&
      (!search || r.variant_id.toLowerCase().includes(search.toLowerCase())),
  );
  return (
    <>
      <FilterBar>
        <SearchInput value={search} onChange={setSearch} label="Search variants" placeholder="Search variants" />
        <FilterSelect label="Verdict" value={verdict} onChange={setVerdict} options={VERDICTS} allLabel="All verdicts" />
        <span className="text-xs text-text-muted">
          {shown.length} of {results.length}
        </span>
      </FilterBar>
      <div className="overflow-hidden rounded-xl border border-border bg-surface shadow-card">
        {shown.length === 0 ? (
          <EmptyState message="No results match these filters." />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Scenario variant</TableHead>
                <TableHead>Tags</TableHead>
                <TableHead>Verdict</TableHead>
                <TableHead>Expected → actual status</TableHead>
                <TableHead>Failed checks</TableHead>
                <TableHead>Intent</TableHead>
                <TableHead className="text-right">Latency</TableHead>
                <TableHead className="text-right">Cost</TableHead>
                <TableHead>Check set</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {shown.map((r) => {
                const scenario = scenarios.get(r.scenario_id);
                const expected = scenario?.expected_status;
                const matches = expected === undefined || expected === r.final_status;
                return (
                  <TableRow key={r.variant_id}>
                    <TableCell>
                      <Link
                        to={`/runs/${runId}/results/${encodeURIComponent(r.variant_id)}`}
                        className="font-medium text-blue-strong hover:underline"
                      >
                        {r.scenario_id}
                      </Link>
                      <span className="ml-1.5 text-xs text-text-muted">{r.persona}</span>
                    </TableCell>
                    <TableCell>{scenario ? <Tags tags={scenario.tags} /> : "—"}</TableCell>
                    <TableCell>
                      <StatusBadge style={verdictStyle(r.verdict)} />
                    </TableCell>
                    <TableCell>
                      <span className="text-text-muted">{humanize(expected)}</span>
                      <span className="text-text-muted"> → </span>
                      <span className={matches ? "text-navy" : "font-medium text-critical-strong"}>
                        {humanize(r.final_status)}
                      </span>
                    </TableCell>
                    <TableCell>
                      <FailureCounts r={r} />
                    </TableCell>
                    <TableCell className="text-navy">{humanize(r.intent)}</TableCell>
                    <TableCell className="text-right tabular-nums">{ms(r.latency_ms)}</TableCell>
                    <TableCell className="text-right tabular-nums">{usd(r.estimated_cost_usd)}</TableCell>
                    <TableCell className="text-text-muted">{checkSet(r.check_set_version)}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </div>
    </>
  );
}

function FailedChecks({ runId, checks }: { runId: number; checks: FailedCheck[] }) {
  const order = { critical: 0, major: 1, minor: 2 } as const;
  const sorted = [...checks].sort((a, b) => order[a.severity] - order[b.severity]);
  if (!sorted.length) return <EmptyState message="No failed checks in this run." />;
  return (
    <ul className="flex flex-col gap-3">
      {sorted.map((c, i) => (
        <li key={`${c.variant_id}-${c.name}-${c.target ?? i}`} className="rounded-xl border border-border bg-surface p-4 shadow-card">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge style={severityStyle(c.severity)} />
            <span className="font-mono text-xs text-navy">{c.name}</span>
            {c.target && <span className="text-xs text-text-muted">{c.target}</span>}
            {c.source === "judge" && <span className="text-xs text-text-muted">(judge, not gated)</span>}
            <Link
              to={`/runs/${runId}/results/${encodeURIComponent(c.variant_id)}`}
              className="ml-auto text-xs font-medium text-blue-strong hover:underline"
            >
              {c.variant_id}
            </Link>
          </div>
          <p className="mt-2 text-sm text-navy">{c.reason}</p>
          <div className="mt-2 text-xs">
            <span className="text-text-muted">Expected: </span>
            <span className="text-navy"><Value value={c.expected} /></span>
          </div>
          <div className="mt-3">
            <RawDetails label="What actually happened" value={c.actual} />
          </div>
        </li>
      ))}
    </ul>
  );
}

const RATES: { key: keyof RunMetrics; label: string }[] = [
  { key: "task_success", label: "Task success" },
  { key: "policy_compliance", label: "Policy compliance" },
  { key: "intent_accuracy", label: "Intent accuracy" },
  { key: "tool_selection_accuracy", label: "Tool selection" },
  { key: "tool_argument_accuracy", label: "Tool arguments" },
  { key: "escalation_accuracy", label: "Escalation accuracy" },
  { key: "failure_recovery", label: "Failure recovery" },
  { key: "crash_rate", label: "Crash rate" },
  { key: "judge_behavior", label: "Judge: behaviour (reported only)" },
];

function MetricsTab({ m }: { m: RunMetrics }) {
  const failed = Object.entries(m.failed_checks);
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Panel title="Rates" description="Each rate only counts variants it applies to" flush>
        <Table>
          <TableBody>
            {RATES.map(({ key, label }) => {
              const r = m[key] as { passed: number; total: number; rate: number | null };
              return (
                <TableRow key={key}>
                  <TableCell className="text-navy">{label}</TableCell>
                  <TableCell className="text-right font-medium tabular-nums text-navy">{percent(r.rate, 1)}</TableCell>
                  <TableCell className="text-right text-text-muted tabular-nums">{fraction(r.passed, r.total)}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Panel>
      <div className="flex flex-col gap-4">
        <Panel title="Failed checks by name">
          {failed.length ? (
            <ul className="flex flex-col gap-1.5 text-sm">
              {failed.map(([name, n]) => (
                <li key={name} className="flex justify-between gap-4">
                  <span className="font-mono text-xs text-navy">{name}</span>
                  <span className="tabular-nums text-text-muted">{n}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-text-muted">None.</p>
          )}
        </Panel>
        <Panel title="Task success by tag">
          <ul className="flex flex-col gap-1.5 text-sm">
            {Object.entries(m.task_success_by_tag).map(([tag, r]) => (
              <li key={tag} className="flex justify-between gap-4">
                <span className="text-navy">{tag}</span>
                <span className="tabular-nums text-text-muted">{fraction(r.passed, r.total)}</span>
              </li>
            ))}
          </ul>
        </Panel>
        <Panel title="Escalations">
          <KeyValues
            items={[
              { label: "Missed escalations", value: m.missed_escalations },
              { label: "Unnecessary escalations", value: m.unnecessary_escalations },
              { label: "Forbidden tool violations", value: m.forbidden_tool_violations },
              { label: "Tool calls", value: m.tool_calls },
            ]}
          />
        </Panel>
      </div>
    </div>
  );
}

export function RunDetail() {
  const runId = Number(useParams().runId);
  const run = useTestRun(runId);
  const results = useRunResults(runId);
  const failed = useRunFailedChecks(runId);
  const scenarios = useScenarios();
  const scenarioMap = useMemo(
    () => new Map((scenarios.data ?? []).map((s) => [s.id, s])),
    [scenarios.data],
  );

  return (
    <>
      <Link to="/runs" className="mb-3 inline-flex items-center gap-1 text-xs font-medium text-text-muted hover:text-navy">
        <ArrowLeft aria-hidden className="size-3.5" /> Test runs
      </Link>
      <QueryState query={run}>
        {(r) => (
          <>
            <PageHeader
              title={`Run #${r.id}`}
              description={`${r.agent_version} · ${r.variant_count} variants · checks ${checkSet(r.check_set_version)} · judge ${r.judge_model ?? "off"} · ${dateTime(r.created_at)}`}
              actions={<StatusBadge style={runStatusStyle(r.status)} />}
            />
            {r.error && (
              <div role="alert" className="mb-4 rounded-lg border border-critical/25 bg-critical-soft px-4 py-3 text-sm text-critical-strong">
                {r.error}
              </div>
            )}
            <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-5">
              <KpiCard label="Task success" value={percent(r.metrics.task_success.rate)} detail={fraction(r.metrics.passed, r.metrics.variants)} />
              <KpiCard label="Policy compliance" value={percent(r.metrics.policy_compliance.rate)} detail={fraction(r.metrics.policy_compliance.passed, r.metrics.policy_compliance.total)} />
              <KpiCard label="Mean latency" value={ms(r.metrics.latency.mean_ms)} detail={`p95 ${ms(r.metrics.latency.p95_ms)}`} />
              <KpiCard label="Claude calls" value={r.metrics.tokens.llm_calls} detail={`${r.metrics.tokens.input_tokens + r.metrics.tokens.output_tokens} tokens`} />
              <KpiCard label="Estimated cost" accent="tan" value={usd(r.metrics.estimated_cost_usd)} />
            </div>
            <Tabs defaultValue="results">
              <TabsList>
                <TabsTrigger value="results">Results</TabsTrigger>
                <TabsTrigger value="failed">Failed checks</TabsTrigger>
                <TabsTrigger value="metrics">Metrics</TabsTrigger>
                <TabsTrigger value="config">Configuration</TabsTrigger>
              </TabsList>
              <TabsContent value="results" className="mt-4">
                <QueryState query={results} isEmpty={(d) => d.length === 0} emptyMessage="No results recorded yet.">
                  {(data) => <ResultsTable runId={runId} results={data} scenarios={scenarioMap} />}
                </QueryState>
              </TabsContent>
              <TabsContent value="failed" className="mt-4">
                <QueryState query={failed}>{(data) => <FailedChecks runId={runId} checks={data} />}</QueryState>
              </TabsContent>
              <TabsContent value="metrics" className="mt-4">
                <MetricsTab m={r.metrics} />
              </TabsContent>
              <TabsContent value="config" className="mt-4">
                <div className="grid gap-4 lg:grid-cols-2">
                  <Panel title="Agent configuration" description={`Version ${r.agent_version}, as this run used it`}>
                    <KeyValues items={Object.entries(r.agent_config).map(([k, v]) => ({ label: humanize(k), value: <Value value={v} /> }))} />
                  </Panel>
                  <Panel title="Selection">
                    <KeyValues items={Object.entries(r.selection).map(([k, v]) => ({ label: humanize(k), value: <Value value={v} /> }))} />
                    {r.rerun_id !== null && (
                      <p className="mt-4 text-xs text-text-muted">
                        Repetition {r.repetition} of rerun {r.rerun_id} ({r.rerun_side} side).
                      </p>
                    )}
                  </Panel>
                </div>
              </TabsContent>
            </Tabs>
          </>
        )}
      </QueryState>
    </>
  );
}
