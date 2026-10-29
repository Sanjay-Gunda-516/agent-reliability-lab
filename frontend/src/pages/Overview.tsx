import {
  Activity,
  AlertOctagon,
  Coins,
  Gauge,
  GitBranch,
  Hash,
  LifeBuoy,
  ListChecks,
  ShieldCheck,
  Target,
  Timer,
  Wrench,
} from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";

import { useApprovals, useComparisons, useTestRuns } from "@/api/queries";
import type { Rate } from "@/api/types";
import { StackedBars, TrendLine } from "@/components/common/charts";
import { KpiCard } from "@/components/common/KpiCard";
import { PageHeader } from "@/components/common/PageHeader";
import { Panel } from "@/components/common/Panel";
import { EmptyState, ErrorState, LoadingState } from "@/components/common/QueryState";
import { StatusBadge } from "@/components/common/StatusBadge";
import { VersionSelect } from "@/components/common/VersionSelect";
import { CHART } from "@/lib/chartColors";
import { compact, count, dateTime, fraction, ms, percent, usd } from "@/lib/format";
import { comparableEarlierRun, completedRuns, type CompletedRun } from "@/lib/runs";
import { approvalStyle, gateStyle, runStatusStyle } from "@/lib/status";

type RateKey =
  | "task_success"
  | "policy_compliance"
  | "intent_accuracy"
  | "tool_selection_accuracy"
  | "escalation_accuracy"
  | "failure_recovery"
  | "crash_rate";

const RATE_KPIS: { key: RateKey; label: string; icon: typeof Target; lowerIsBetter?: boolean }[] = [
  { key: "task_success", label: "Task success", icon: Target },
  { key: "policy_compliance", label: "Policy compliance", icon: ShieldCheck },
  { key: "intent_accuracy", label: "Intent accuracy", icon: ListChecks },
  { key: "tool_selection_accuracy", label: "Tool selection", icon: Wrench },
  { key: "escalation_accuracy", label: "Escalation accuracy", icon: GitBranch },
  { key: "failure_recovery", label: "Failure recovery", icon: LifeBuoy },
  { key: "crash_rate", label: "Crash rate", icon: AlertOctagon, lowerIsBetter: true },
];

function rateTrend(current: Rate, earlier: Rate | undefined, runId: number | undefined, lowerIsBetter?: boolean) {
  if (!earlier || current.rate === null || earlier.rate === null || runId === undefined) return undefined;
  const points = (current.rate - earlier.rate) * 100;
  const rounded = Math.round(points * 10) / 10;
  if (rounded === 0) return { text: "no change", good: null, title: `Same as run #${runId}` };
  const better = lowerIsBetter ? rounded < 0 : rounded > 0;
  return {
    text: `${rounded > 0 ? "▲" : "▼"} ${Math.abs(rounded)} pts`,
    good: better,
    title: `Compared with run #${runId} (same version, variant count and check set)`,
  };
}

function Kpis({ latest, earlier, total }: { latest: CompletedRun; earlier?: CompletedRun; total: number }) {
  const m = latest.metrics;
  const tokens = m.tokens.input_tokens + m.tokens.output_tokens;
  const cacheTokens = m.tokens.cache_creation_input_tokens + m.tokens.cache_read_input_tokens;
  return (
    <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
      <KpiCard label="Test runs" value={count(total)} icon={Hash} detail="completed, excluding reruns" />
      {RATE_KPIS.map(({ key, label, icon, lowerIsBetter }) => (
        <KpiCard
          key={key}
          label={label}
          icon={icon}
          value={percent(m[key].rate)}
          detail={fraction(m[key].passed, m[key].total)}
          trend={rateTrend(m[key], earlier?.metrics[key], earlier?.id, lowerIsBetter)}
        />
      ))}
      <KpiCard label="Average latency" icon={Timer} value={ms(m.latency.mean_ms)} detail={`p95 ${ms(m.latency.p95_ms)}`} />
      <KpiCard
        label="Tokens"
        icon={Activity}
        value={compact(tokens)}
        detail={`${compact(cacheTokens)} cached · ${count(m.tokens.llm_calls)} calls`}
      />
      <KpiCard
        label="Estimated cost"
        icon={Coins}
        accent="tan"
        value={usd(m.estimated_cost_usd)}
        detail={m.estimated_cost_usd === null ? "model price unknown" : "list prices, agent only"}
      />
      <KpiCard label="Tool calls" icon={Gauge} value={count(m.tool_calls)} detail={`${count(m.forbidden_tool_violations)} forbidden`} />
    </div>
  );
}

function Charts({ runs }: { runs: CompletedRun[] }) {
  const points = runs.map((r) => ({
    run: `#${r.id}`,
    taskSuccess: r.metrics.task_success.rate === null ? null : r.metrics.task_success.rate * 100,
    passed: r.metrics.passed,
    failed: r.metrics.failed,
    errored: r.metrics.errored,
    latency: r.metrics.latency.mean_ms,
    cost: r.metrics.estimated_cost_usd === null ? null : Number(r.metrics.estimated_cost_usd),
  }));
  if (points.length < 2) {
    return (
      <Panel title="Trends">
        <EmptyState
          message="Trends appear after two completed runs."
          hint="Each point is one completed test run; nothing is interpolated or estimated."
        />
      </Panel>
    );
  }
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <Panel title="Task success by run" description="Share of variants that passed, per completed run">
        <TrendLine
          data={points}
          xKey="run"
          series={[{ key: "taskSuccess", label: "Task success", color: CHART.blue }]}
          format={(v) => `${Math.round(v)}%`}
          summary={`Task success for ${points.length} runs, from ${points[0]?.taskSuccess ?? 0}% to ${points.at(-1)?.taskSuccess ?? 0}%.`}
        />
      </Panel>
      <Panel title="Verdicts by run" description="Passed, failed and crashed variants">
        <StackedBars
          data={points}
          xKey="run"
          series={[
            { key: "passed", label: "Passed", color: CHART.blue },
            { key: "failed", label: "Failed", color: CHART.tan },
            { key: "errored", label: "Crashed", color: CHART.critical },
          ]}
          integers
          summary={`Passed, failed and crashed variant counts for ${points.length} runs.`}
        />
      </Panel>
      <Panel title="Mean latency by run">
        <TrendLine
          data={points}
          xKey="run"
          series={[{ key: "latency", label: "Mean latency", color: CHART.navy }]}
          format={(v) => ms(v)}
          summary={`Mean latency per run for ${points.length} runs.`}
        />
      </Panel>
      <Panel title="Estimated cost by run" description="Agent calls only, at list prices">
        <StackedBars
          data={points}
          xKey="run"
          series={[{ key: "cost", label: "Cost", color: CHART.tan }]}
          format={(v) => usd(v, 3)}
          summary={`Estimated cost per run for ${points.length} runs.`}
        />
      </Panel>
    </div>
  );
}

function RecentLists({ version }: { version: string }) {
  const runs = useTestRuns({ include_reruns: false, agent_version: version || undefined, limit: 5 });
  const reports = useComparisons();
  const pending = useApprovals("pending");
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
      <Panel title="Recent runs" action={<Link className="text-xs font-medium text-blue-strong hover:underline" to="/runs">All runs</Link>} flush>
        {runs.isPending ? (
          <div className="p-5"><LoadingState rows={3} /></div>
        ) : runs.isError ? (
          <div className="p-5"><ErrorState error={runs.error} /></div>
        ) : runs.data.length === 0 ? (
          <EmptyState message="No test runs yet." />
        ) : (
          <ul className="divide-y divide-border">
            {runs.data.map((r) => (
              <li key={r.id}>
                <Link to={`/runs/${r.id}`} className="flex items-center justify-between gap-3 px-5 py-3 transition-colors duration-200 hover:bg-surface-muted">
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-navy">Run #{r.id} · {r.agent_version}</div>
                    <div className="text-xs text-text-muted">
                      {dateTime(r.created_at)} · {r.metrics ? `${fraction(r.metrics.passed, r.metrics.variants)} passed` : `${r.variant_count} variants`}
                    </div>
                  </div>
                  <StatusBadge style={runStatusStyle(r.status)} />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Panel>
      <Panel title="Recent regression reports" action={<Link className="text-xs font-medium text-blue-strong hover:underline" to="/regression">All reports</Link>} flush>
        {reports.isPending ? (
          <div className="p-5"><LoadingState rows={3} /></div>
        ) : reports.isError ? (
          <div className="p-5"><ErrorState error={reports.error} /></div>
        ) : reports.data.length === 0 ? (
          <EmptyState message="No regression reports available." />
        ) : (
          <ul className="divide-y divide-border">
            {reports.data.slice(0, 5).map((r) => (
              <li key={r.id}>
                <Link to={`/regression/${r.id}`} className="flex items-center justify-between gap-3 px-5 py-3 transition-colors duration-200 hover:bg-surface-muted">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium text-navy">
                      {r.candidate_agent_version} vs {r.baseline_agent_version}
                    </div>
                    <div className="text-xs text-text-muted">
                      Report #{r.id}{r.revision > 1 ? ` · revision ${r.revision}` : ""} · {dateTime(r.created_at)}
                    </div>
                  </div>
                  <StatusBadge style={gateStyle(r.gate_status)} />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Panel>
      <Panel title="Pending approvals" action={<Link className="text-xs font-medium text-blue-strong hover:underline" to="/approvals">Review</Link>} flush>
        {pending.isPending ? (
          <div className="p-5"><LoadingState rows={2} /></div>
        ) : pending.isError ? (
          <div className="p-5"><ErrorState error={pending.error} /></div>
        ) : pending.data.length === 0 ? (
          <EmptyState message="No approvals waiting." />
        ) : (
          <ul className="divide-y divide-border">
            {pending.data.slice(0, 5).map((a) => (
              <li key={a.id} className="flex items-center justify-between gap-3 bg-warning-soft/40 px-5 py-3">
                <div className="min-w-0">
                  <div className="text-sm font-medium text-navy">
                    {a.tool_name} · order {a.order_id ?? "—"}
                  </div>
                  <div className="text-xs text-text-muted">{usd(a.refund_amount, 2)} · {dateTime(a.created_at)}</div>
                </div>
                <StatusBadge style={approvalStyle(a.status)} />
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}

export function Overview() {
  const [version, setVersion] = useState("");
  const allRuns = useTestRuns({ include_reruns: false, agent_version: version || undefined });
  const runs = useMemo(() => completedRuns(allRuns.data ?? []), [allRuns.data]);
  const latest = runs.at(-1);
  const earlier = latest ? comparableEarlierRun(runs, latest) : undefined;

  return (
    <>
      <PageHeader
        title="Overview"
        description={
          latest
            ? `Metrics from the latest completed run, #${latest.id} (${latest.agent_version}, ${latest.variant_count} variants, checks ${latest.check_set_version ?? "unknown"}).`
            : "How the support agent is doing, from stored test runs."
        }
        actions={<VersionSelect value={version} onChange={setVersion} />}
      />
      <div className="flex flex-col gap-6">
        {allRuns.isPending ? (
          <LoadingState rows={4} label="Loading runs" />
        ) : allRuns.isError ? (
          <ErrorState error={allRuns.error} onRetry={() => void allRuns.refetch()} />
        ) : !latest ? (
          <Panel>
            <EmptyState
              message="No test runs yet."
              hint="Start one with `python -m app.evaluation run` or POST /test-runs; its metrics will appear here."
            />
          </Panel>
        ) : (
          <>
            <Kpis latest={latest} earlier={earlier} total={runs.length} />
            <Charts runs={runs} />
          </>
        )}
        <RecentLists version={version} />
      </div>
    </>
  );
}
