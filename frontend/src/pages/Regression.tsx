import { ArrowLeft, ArrowRight, Check, X } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { useComparison, useComparisons, useReruns } from "@/api/queries";
import type {
  Category,
  ComparisonVariant,
  MetricDelta,
  Observation,
  ReportDetail,
  ReportSummary,
} from "@/api/types";
import { Mono } from "@/components/common/Details";
import { FilterBar, FilterSelect } from "@/components/common/Filters";
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
import { checkSet, dateTime, humanize, ms, signed, usd } from "@/lib/format";
import { CATEGORIES, categoryStyle, gateStyle, TONE_CLASSES, verdictStyle } from "@/lib/status";
import { cn } from "@/lib/utils";

const CATEGORY_ORDER: Category[] = [
  "confirmed_regression",
  "regression",
  "noisy",
  "improvement",
  "unchanged",
  "scenario_changed",
  "checks_changed",
  "missing_in_candidate",
  "new_in_candidate",
];

const sets = (v: (string | null)[]) => (v.length ? v.map(checkSet).join(" + ") : "unknown");

function CategoryCounts({ counts }: { counts: ReportSummary["counts"] }) {
  const present = CATEGORY_ORDER.filter((c) => (counts[c] ?? 0) > 0);
  if (!present.length) return <span className="text-text-muted">—</span>;
  return (
    <span className="flex flex-wrap gap-1.5">
      {present.map((c) => (
        <StatusBadge key={c} style={CATEGORIES[c]} label={`${counts[c]} ${CATEGORIES[c].label.toLowerCase()}`} />
      ))}
    </span>
  );
}

// ---- list -------------------------------------------------------------------------

export function RegressionList() {
  const reports = useComparisons();
  return (
    <>
      <PageHeader
        title="Regression"
        description="Candidate agent versions compared with a baseline on the same scenarios. The gate is deterministic: no LLM decides it."
      />
      <Panel flush>
        <QueryState
          query={reports}
          padded
          isEmpty={(d) => d.length === 0}
          emptyMessage="No regression reports available."
          emptyHint="Create one with `python -m app.evaluation compare <baseline-run> <candidate-run>` or POST /comparisons."
        >
          {(data) => (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Report</TableHead>
                  <TableHead>Candidate vs baseline</TableHead>
                  <TableHead>Gate</TableHead>
                  <TableHead>Variants</TableHead>
                  <TableHead>Check sets</TableHead>
                  <TableHead>Created</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell>
                      <Link to={`/regression/${r.id}`} className="font-medium text-blue-strong hover:underline">
                        #{r.id}
                      </Link>
                      {r.revision > 1 && (
                        <span className="ml-2 text-xs text-text-muted">revision {r.revision} of #{r.root_report_id}</span>
                      )}
                    </TableCell>
                    <TableCell className="text-navy">
                      {r.candidate_agent_version} <span className="text-text-muted">(run #{r.candidate_run_id})</span> vs{" "}
                      {r.baseline_agent_version} <span className="text-text-muted">(run #{r.baseline_run_id})</span>
                    </TableCell>
                    <TableCell><StatusBadge style={gateStyle(r.gate_status)} /></TableCell>
                    <TableCell className="whitespace-normal"><CategoryCounts counts={r.counts} /></TableCell>
                    <TableCell className="text-text-muted">{sets(r.baseline_check_set_versions)} · {sets(r.candidate_check_set_versions)}</TableCell>
                    <TableCell className="text-text-muted">{dateTime(r.created_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </QueryState>
      </Panel>
    </>
  );
}

// ---- detail -----------------------------------------------------------------------

const METRIC_LABELS: Record<string, string> = {
  latency_mean_ms: "Mean latency",
  latency_p95_ms: "p95 latency",
  estimated_cost_usd: "Estimated cost",
  llm_calls: "Claude calls",
  cache_creation_input_tokens: "Cache write tokens",
  cache_read_input_tokens: "Cache read tokens",
};

function deltaTone(d: MetricDelta): "good" | "bad" | "same" {
  if (d.delta === null || d.delta === 0) return "same";
  return (d.delta > 0) === (d.better === "higher") ? "good" : "bad";
}

function formatValue(d: MetricDelta, v: number | null): string {
  if (v === null) return "—";
  if (d.unit === "pp") return `${v.toFixed(v % 1 ? 1 : 0)}%`;
  if (d.unit === "ms") return ms(v);
  if (d.unit === "usd") return usd(v);
  return v.toLocaleString("en-US");
}

function formatDelta(d: MetricDelta): string {
  if (d.delta === null) return "—";
  if (d.unit === "pp") return signed(d.delta, " pts", 1);
  if (d.unit === "ms") return `${signed(d.delta, " ms", 0)}${d.change_pct !== null ? ` (${signed(d.change_pct, "%", 1)})` : ""}`;
  if (d.unit === "usd") return `${signed(d.delta, "", 4).replace(/^([+−±])/, "$1$")}${d.change_pct !== null ? ` (${signed(d.change_pct, "%", 1)})` : ""}`;
  return `${signed(d.delta, "", 0)}${d.change_pct !== null ? ` (${signed(d.change_pct, "%", 1)})` : ""}`;
}

function Side({ title, version, runId, sets: s, passRate }: { title: string; version: string; runId: number; sets: (string | null)[]; passRate?: MetricDelta }) {
  return (
    <div className="rounded-xl border border-border bg-surface p-4 shadow-card">
      <div className="text-xs font-medium text-text-muted">{title}</div>
      <div className="mt-1 text-lg font-semibold text-navy">{version}</div>
      <div className="mt-1 text-xs text-text-muted">
        <Link className="text-blue-strong hover:underline" to={`/runs/${runId}`}>Run #{runId}</Link> · checks {sets(s)}
      </div>
      {passRate && (
        <div className="mt-3 text-2xl font-semibold text-navy tabular-nums">
          {formatValue(passRate, title === "Baseline" ? passRate.baseline : passRate.candidate)}
          <span className="ml-2 text-xs font-normal text-text-muted">
            task success · {title === "Baseline" ? passRate.baseline_detail : passRate.candidate_detail}
          </span>
        </div>
      )}
    </div>
  );
}

function Comparison({ report }: { report: ReportDetail }) {
  const task = report.metric_deltas.find((d) => d.name === "task_success");
  const tone = task ? deltaTone(task) : "same";
  return (
    <div className="grid items-stretch gap-4 md:grid-cols-[1fr_auto_1fr]">
      <Side title="Baseline" version={report.baseline_agent_version} runId={report.baseline_run_id} sets={report.baseline_check_set_versions} passRate={task} />
      <div className="flex flex-col items-center justify-center gap-1 px-2 text-center">
        <ArrowRight aria-hidden className="size-5 text-text-muted" />
        <span className={cn("text-lg font-semibold tabular-nums", tone === "bad" ? "text-critical-strong" : tone === "good" ? "text-success-strong" : "text-neutral-strong")}>
          {task ? formatDelta(task) : "—"}
        </span>
        <span className="text-xs text-text-muted">task success</span>
      </div>
      <Side title="Candidate" version={report.candidate_agent_version} runId={report.candidate_run_id} sets={report.candidate_check_set_versions} passRate={task} />
    </div>
  );
}

function Gate({ report }: { report: ReportDetail }) {
  const t = report.thresholds;
  return (
    <Panel
      title="Gate"
      description={`Fails on ${t.fail_on_new_critical ? "new critical failures or " : ""}a task-success drop over ${t.max_task_success_drop_pp} pts · warns on cost or latency up more than ${t.max_cost_increase_pct}% / ${t.max_latency_increase_pct}%`}
      action={<StatusBadge style={gateStyle(report.gate.status)} />}
    >
      {report.gate.reasons.length === 0 ? (
        <p className="text-sm text-success-strong">No rule failed or warned.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {report.gate.reasons.map((r) => (
            <li key={r.code} className={cn("rounded-lg border px-3 py-2", TONE_CLASSES[r.level === "fail" ? "critical" : "warning"])}>
              <div className="flex flex-wrap items-center gap-2 text-xs font-semibold uppercase tracking-wide">
                {r.level === "fail" ? "Fail" : "Warning"} <Mono>{r.code}</Mono>
              </div>
              <p className="mt-0.5 text-sm">{r.message}</p>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function Deltas({ deltas }: { deltas: MetricDelta[] }) {
  const shown = deltas.filter((d) => d.baseline !== null || d.candidate !== null);
  return (
    <Panel title="Metric deltas" description="Over the variants the gate counts" flush>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Metric</TableHead>
            <TableHead className="text-right">Baseline</TableHead>
            <TableHead className="text-center">Change</TableHead>
            <TableHead>Candidate</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {shown.map((d) => {
            const tone = deltaTone(d);
            return (
              <TableRow key={d.name}>
                <TableCell className="text-navy">{METRIC_LABELS[d.name] ?? humanize(d.name)}</TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatValue(d, d.baseline)}
                  {d.baseline_detail && <span className="ml-1.5 text-xs text-text-muted">{d.baseline_detail}</span>}
                </TableCell>
                <TableCell className={cn("text-center text-xs font-medium tabular-nums", tone === "bad" ? "text-critical-strong" : tone === "good" ? "text-success-strong" : "text-text-muted")}>
                  {formatDelta(d)}
                  <span className="sr-only">{tone === "bad" ? " (worse)" : tone === "good" ? " (better)" : ""}</span>
                </TableCell>
                <TableCell className="tabular-nums">
                  {formatValue(d, d.candidate)}
                  {d.candidate_detail && <span className="ml-1.5 text-xs text-text-muted">{d.candidate_detail}</span>}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </Panel>
  );
}

function ObservationDots({ label, observations, reproduced, counted, what }: { label: string; observations: Observation[]; reproduced: number; counted: number; what: string }) {
  return (
    <div>
      <div className="text-xs font-medium text-text-muted">{label}</div>
      <div className="mt-1 flex items-center gap-1.5">
        {observations.length === 0 && <span className="text-xs text-text-muted">not rerun</span>}
        {observations.map((o, i) => (
          <span
            key={o.result_id ?? i}
            title={`Run #${o.test_run_id ?? "?"}: ${o.verdict}${o.counted ? "" : " (not counted: different scenario or checks)"}`}
            className={cn(
              "flex size-6 items-center justify-center rounded-md border",
              !o.counted ? "border-dashed text-text-muted" : o.verdict === "pass" ? TONE_CLASSES.success : TONE_CLASSES.critical,
            )}
          >
            {o.verdict === "pass" ? <Check aria-hidden className="size-3.5" /> : <X aria-hidden className="size-3.5" />}
            <span className="sr-only">{o.verdict}</span>
          </span>
        ))}
        {counted > 0 && (
          <span className="ml-1 text-xs text-navy tabular-nums">
            {reproduced}/{counted} {what}
          </span>
        )}
      </div>
    </div>
  );
}

function prettyCheck(key: string): { name: string; target: string | null } {
  const m = /^(.*?)\[(.*)\]$/.exec(key);
  return m ? { name: m[1] ?? key, target: m[2] ?? null } : { name: key, target: null };
}

function CheckList({ label, keys, tone }: { label: string; keys: string[]; tone: "critical" | "warning" | "success" }) {
  if (!keys.length) return null;
  return (
    <div>
      <div className="text-xs font-medium text-text-muted">{label}</div>
      <ul className="mt-1 flex flex-col gap-1">
        {keys.map((k) => {
          const { name, target } = prettyCheck(k);
          return (
            <li key={k} className={cn("w-fit rounded-md border px-2 py-0.5 text-xs", TONE_CLASSES[tone])}>
              <span className="font-medium">{humanize(name.replace(/^tools\./, ""))}</span>
              {target && <span className="ml-1 font-mono opacity-80">{target}</span>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function VariantCard({ v }: { v: ComparisonVariant }) {
  const style = categoryStyle(v.category);
  const e = v.rerun_evidence;
  const flip = e?.first_category === "improvement" ? "repeated the improvement" : "repeated the regression";
  return (
    <li className={cn("rounded-xl border bg-surface p-4 shadow-card", v.category === "confirmed_regression" ? "border-critical/40" : "border-border")}>
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge style={style} />
        <span className="font-medium text-navy">{v.scenario_id}</span>
        <span className="text-xs text-text-muted">{v.variant_id.split(":")[1]}</span>
        {!v.gated && <span className="text-xs text-text-muted">· not counted by the gate</span>}
        <span className="ml-auto flex items-center gap-1.5">
          {v.baseline_verdict && <StatusBadge style={verdictStyle(v.baseline_verdict)} />}
          <ArrowRight aria-label="then" className="size-3.5 text-text-muted" />
          {v.candidate_verdict && <StatusBadge style={verdictStyle(v.candidate_verdict)} />}
        </span>
      </div>
      <p className="mt-2 text-sm text-navy">{v.explanation}</p>
      <div className="mt-3 grid gap-3 sm:grid-cols-3">
        <CheckList label="New critical failures" keys={v.new_critical_failures} tone="critical" />
        <CheckList label="Newly failing checks" keys={v.new_failed_checks.filter((k) => !v.new_critical_failures.includes(k))} tone="warning" />
        <CheckList label="Fixed checks" keys={v.fixed_checks} tone="success" />
      </div>
      {e && (
        <div className="mt-3 grid gap-3 rounded-lg bg-surface-muted p-3 sm:grid-cols-2">
          <ObservationDots label="Candidate reruns" observations={e.candidate} reproduced={e.candidate_reproduced} counted={e.candidate_counted} what={flip.replace("repeated the ", "")} />
          <ObservationDots label="Baseline reruns" observations={e.baseline} reproduced={e.baseline_reproduced} counted={e.baseline_counted} what="same change" />
          <p className="text-xs text-text-muted sm:col-span-2">
            First seen as {categoryStyle(e.first_category).label.toLowerCase()}; after reruns: {categoryStyle(e.outcome).label.toLowerCase()}.
          </p>
        </div>
      )}
      {v.needs_rerun && (
        <p className="mt-3 text-xs text-warning-strong">Seen once. Rerun it to tell a real regression from model noise.</p>
      )}
    </li>
  );
}

function Variants({ variants }: { variants: ComparisonVariant[] }) {
  const [category, setCategory] = useState("");
  const options = CATEGORY_ORDER.filter((c) => variants.some((v) => v.category === c)).map((c) => ({ value: c, label: CATEGORIES[c].label }));
  const shown = variants
    .filter((v) => !category || v.category === category)
    .sort((a, b) => CATEGORY_ORDER.indexOf(a.category) - CATEGORY_ORDER.indexOf(b.category));
  return (
    <section>
      <div className="mb-3 flex items-center justify-between gap-4">
        <h2 className="text-sm font-semibold text-navy">Variants</h2>
      </div>
      <FilterBar>
        <FilterSelect label="Category" value={category} onChange={setCategory} options={options} allLabel="All categories" />
        <span className="text-xs text-text-muted">{shown.length} of {variants.length}</span>
      </FilterBar>
      {shown.length === 0 ? (
        <Panel><EmptyState message="No variants in this category." /></Panel>
      ) : (
        <ul className="flex flex-col gap-3">{shown.map((v) => <VariantCard key={v.variant_id} v={v} />)}</ul>
      )}
    </section>
  );
}

function Revisions({ report }: { report: ReportDetail }) {
  const root = report.root_report_id ?? report.id;
  const reruns = useReruns(root);
  if (report.revisions.length < 2 && !reruns.data?.length) return null;
  return (
    <nav aria-label="Report revisions" className="mb-4 flex flex-wrap items-center gap-2">
      {report.revisions.map((id, i) => (
        <Link
          key={id}
          to={`/regression/${id}`}
          aria-current={id === report.id ? "page" : undefined}
          className={cn(
            "rounded-md border px-2.5 py-1 text-xs font-medium transition-colors duration-200",
            id === report.id ? "border-blue/40 bg-blue-soft text-navy" : "border-border bg-surface text-text-muted hover:text-navy",
          )}
        >
          Revision {i + 1}{i === 0 ? " · original" : " · after rerun"}
        </Link>
      ))}
      {reruns.data?.map((r) => (
        <span key={r.id} className="text-xs text-text-muted">
          Rerun {r.id}: {r.repetitions}× {r.sides === "both" ? "both sides" : "candidate"} · {r.status}
        </span>
      ))}
    </nav>
  );
}

export function RegressionDetail() {
  const id = Number(useParams().reportId);
  const report = useComparison(id);
  return (
    <>
      <Link to="/regression" className="mb-3 inline-flex items-center gap-1 text-xs font-medium text-text-muted hover:text-navy">
        <ArrowLeft aria-hidden className="size-3.5" /> Regression
      </Link>
      <QueryState query={report}>
        {(r) => (
          <>
            <PageHeader
              title={`${r.candidate_agent_version} vs ${r.baseline_agent_version}`}
              description={`Report #${r.id}${r.revision > 1 ? `, revision ${r.revision}` : ""} · ${dateTime(r.created_at)}`}
              actions={<StatusBadge style={gateStyle(r.gate_status)} />}
            />
            <Revisions report={r} />
            <div className="flex flex-col gap-6">
              <Comparison report={r} />
              <div className="grid items-start gap-4 xl:grid-cols-2">
                <Gate report={r} />
                <Panel title="Variant categories"><CategoryCounts counts={r.counts} /></Panel>
              </div>
              <Variants variants={r.variants} />
              <Deltas deltas={r.metric_deltas} />
            </div>
          </>
        )}
      </QueryState>
    </>
  );
}
