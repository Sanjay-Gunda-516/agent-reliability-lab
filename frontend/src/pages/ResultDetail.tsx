import { ArrowLeft, Check, X } from "lucide-react";
import type { ReactNode } from "react";
import { Link, useParams } from "react-router";

import { useResult, useScenario, useTraceToolCalls } from "@/api/queries";
import type { CheckView, ResultDetail as Result, ScenarioDetail, ToolCallView, ToolExpectation } from "@/api/types";
import { KeyValues, Mono, RawDetails, Tags, Value } from "@/components/common/Details";
import { PageHeader } from "@/components/common/PageHeader";
import { Panel } from "@/components/common/Panel";
import { LoadingState, QueryState } from "@/components/common/QueryState";
import { StatusBadge } from "@/components/common/StatusBadge";
import { checkSet, count, humanize, ms, shortHash, usd } from "@/lib/format";
import { severityStyle, verdictStyle } from "@/lib/status";
import { cn } from "@/lib/utils";

function describeTool(t: ToolExpectation): string {
  const args = t.args ? Object.entries(t.args).map(([k, v]) => `${k}=${String(v)}`).join(", ") : "";
  const result = t.result && t.result !== "success" && t.result !== "any" ? ` → ${t.result}` : "";
  return `${t.tool}${args ? `(${args})` : ""}${result}`;
}

/** Did the backend's check(s) for this row pass? undefined when no such check ran. */
function verdictFor(checks: CheckView[], names: string[]): boolean | undefined {
  const relevant = checks.filter((c) => names.includes(c.name) && c.source === "deterministic");
  return relevant.length ? relevant.every((c) => c.passed) : undefined;
}

function Row({ label, expected, actual, ok }: { label: string; expected: ReactNode; actual: ReactNode; ok?: boolean }) {
  return (
    <div className="grid grid-cols-1 gap-2 border-b border-border px-5 py-3 last:border-0 md:grid-cols-[10rem_1fr_1fr_6rem] md:items-start">
      <div className="text-xs font-medium text-text-muted">{label}</div>
      <div className="text-sm text-navy">
        <span className="text-xs text-text-muted md:hidden">Expected: </span>
        {expected}
      </div>
      <div className="text-sm text-navy">
        <span className="text-xs text-text-muted md:hidden">Actual: </span>
        {actual}
      </div>
      <div className="md:text-right">
        {ok === undefined ? (
          <span className="text-xs text-text-muted">not checked</span>
        ) : (
          <span className={cn("inline-flex items-center gap-1 text-xs font-medium", ok ? "text-success-strong" : "text-critical-strong")}>
            {ok ? <Check aria-hidden className="size-3.5" /> : <X aria-hidden className="size-3.5" />}
            {ok ? "Matches" : "Differs"}
          </span>
        )}
      </div>
    </div>
  );
}

function ToolCallList({ calls }: { calls: ToolCallView[] }) {
  if (!calls.length) return <span className="text-text-muted">no tool calls</span>;
  return (
    <ol className="flex flex-col gap-1">
      {calls.map((c) => (
        <li key={`${c.sequence}-${c.tool_use_id}`} className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-text-muted tabular-nums">{c.sequence}.</span>
          <Mono>
            {c.name}({Object.entries(c.arguments).filter(([k]) => k === "order_id").map(([, v]) => `order_id=${String(v)}`).join("")})
          </Mono>
          <span
            className={cn(
              "text-xs",
              c.status === "success" ? "text-success-strong" : c.status === "rejected" ? "text-warning-strong" : "text-critical-strong",
            )}
          >
            {c.status}
            {c.error ? ` · ${c.error}` : ""}
          </span>
        </li>
      ))}
    </ol>
  );
}

function ExpectedVsActual({ result, scenario, calls }: { result: Result; scenario: ScenarioDetail; calls: ToolCallView[] | undefined }) {
  const e = scenario.definition.expected;
  const finalState = (result.trace?.final_state ?? {}) as Record<string, Record<string, unknown>>;
  const expectedState = (e.state ?? {}) as Record<string, Record<string, unknown>>;
  // Only show the parts of the final state the scenario has expectations about.
  const relevantState = Object.fromEntries(
    Object.entries(expectedState).map(([kind, ids]) => [
      kind,
      Object.fromEntries(Object.keys(ids).map((id) => [id, finalState[kind]?.[id]])),
    ]),
  );
  const checks = result.checks;
  return (
    <Panel title="Expected vs actual" description="Each row shows whether the backend's check passed" flush>
      <div className="hidden grid-cols-[10rem_1fr_1fr_6rem] gap-2 border-b border-border bg-surface-muted/70 px-5 py-2 text-xs font-medium text-text-muted md:grid">
        <span />
        <span>Expected</span>
        <span>Actual</span>
        <span className="text-right">Check</span>
      </div>
      <Row label="Final status" expected={humanize(e.final_status)} actual={humanize(result.final_status)} ok={verdictFor(checks, ["status.final"])} />
      {(e.escalation_reason || result.escalation_reason) && (
        <Row label="Escalation reason" expected={humanize(e.escalation_reason)} actual={humanize(result.escalation_reason)} ok={verdictFor(checks, ["escalation.reason"])} />
      )}
      {e.intent && <Row label="Intent" expected={humanize(e.intent)} actual={humanize(result.intent)} ok={verdictFor(checks, ["intent"])} />}
      <Row
        label="Required tools"
        expected={e.must_call?.length ? e.must_call.map(describeTool).join(", ") : <span className="text-text-muted">none</span>}
        actual={calls ? <ToolCallList calls={calls} /> : "—"}
        ok={verdictFor(checks, ["tools.selection", "tools.arguments", "tools.result"])}
      />
      <Row
        label="Forbidden tools"
        expected={e.must_not_call?.length ? `never ${e.must_not_call.map(describeTool).join(", ")}` : <span className="text-text-muted">none</span>}
        actual={
          verdictFor(checks, ["tools.forbidden"]) === undefined
            ? "—"
            : verdictFor(checks, ["tools.forbidden"])
              ? "Not attempted"
              : "Attempted"
        }
        ok={verdictFor(checks, ["tools.forbidden"])}
      />
      <Row
        label="Lookup before action"
        expected="each order action follows a lookup of that order"
        actual={verdictFor(checks, ["tools.lookup_before_action"]) === false ? "acted before looking up" : verdictFor(checks, ["tools.lookup_before_action"]) === undefined ? "no order action" : "looked up first"}
        ok={verdictFor(checks, ["tools.lookup_before_action"])}
      />
      {Object.keys(expectedState).length > 0 && (
        <Row label="Final state" expected={<Value value={expectedState} />} actual={<Value value={relevantState} />} ok={verdictFor(checks, ["state.expected"])} />
      )}
    </Panel>
  );
}

function ChecksPanel({ checks }: { checks: CheckView[] }) {
  const order = { critical: 0, major: 1, minor: 2 } as const;
  const failed = checks.filter((c) => !c.passed).sort((a, b) => order[a.severity] - order[b.severity]);
  const passed = checks.filter((c) => c.passed);
  return (
    <Panel title="Checks" description={`${failed.length} failed · ${passed.length} passed`}>
      {failed.length === 0 ? (
        <p className="text-sm text-success-strong">Every check passed.</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {failed.map((c, i) => (
            <li key={`${c.name}-${c.target ?? i}`} className="rounded-lg border border-border p-3">
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge style={severityStyle(c.severity)} />
                <Mono>{c.name}</Mono>
                {c.target && <span className="text-xs text-text-muted">{c.target}</span>}
                {c.source === "judge" && <span className="text-xs text-text-muted">judge · reported only</span>}
              </div>
              <p className="mt-2 text-sm text-navy">{c.reason}</p>
              <p className="mt-1 text-xs">
                <span className="text-text-muted">Expected: </span>
                <span className="text-navy"><Value value={c.expected} /></span>
              </p>
              <div className="mt-2">
                <RawDetails label="Actual (raw)" value={c.actual} />
              </div>
            </li>
          ))}
        </ul>
      )}
      {passed.length > 0 && (
        <details className="group mt-4">
          <summary className="cursor-pointer text-xs font-medium text-text-muted hover:text-navy">Show {passed.length} passed checks</summary>
          <ul className="mt-2 flex flex-col gap-1">
            {passed.map((c, i) => (
              <li key={`${c.name}-${c.target ?? i}`} className="flex items-start gap-2 text-xs">
                <Check aria-hidden className="mt-0.5 size-3.5 shrink-0 text-success-strong" />
                <span><Mono>{c.name}</Mono> <span className="text-text-muted">{c.reason}</span></span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </Panel>
  );
}

function ResultView({ result }: { result: Result }) {
  const scenario = useScenario(result.scenario_id);
  const calls = useTraceToolCalls(result.trace?.execution_id);
  const variant = scenario.data?.variants.find((v) => v.id === result.variant_id);
  const hashChanged = scenario.data && scenario.data.content_hash !== result.scenario_hash;
  return (
    <>
      <PageHeader
        title={result.scenario_id}
        description={scenario.data?.title}
        actions={
          <>
            <span className="text-sm text-text-muted">{result.persona}</span>
            <StatusBadge style={verdictStyle(result.verdict)} />
          </>
        }
      />
      <div className="grid gap-4 xl:grid-cols-[1fr_20rem]">
        <div className="flex min-w-0 flex-col gap-4">
          {variant && (
            <Panel title="Customer said">
              <ol className="flex flex-col gap-2">
                {variant.turns.map((t, i) => (
                  <li key={i} className="rounded-lg bg-blue-soft px-3 py-2 text-sm text-navy">{t}</li>
                ))}
              </ol>
            </Panel>
          )}
          {scenario.isPending ? (
            <LoadingState rows={4} label="Loading expectations" />
          ) : scenario.data ? (
            <>
              {hashChanged && (
                <p role="note" className="rounded-lg border border-warning/35 bg-warning-soft px-4 py-2 text-sm text-warning-strong">
                  This scenario has changed since the run (hash {shortHash(result.scenario_hash)} → {shortHash(scenario.data.content_hash)}). Expectations below are the current ones; the check results are from the run.
                </p>
              )}
              <ExpectedVsActual result={result} scenario={scenario.data} calls={calls.data} />
              <Panel title="Expected behaviour">
                <p className="text-sm text-navy">{scenario.data.definition.expected.behavior}</p>
              </Panel>
            </>
          ) : null}
          {result.final_response && (
            <Panel title="Final reply to the customer">
              <p className="text-sm whitespace-pre-line text-navy">{result.final_response}</p>
            </Panel>
          )}
          {result.error && (
            <div role="alert" className="rounded-lg border border-critical/25 bg-critical-soft px-4 py-3 text-sm text-critical-strong">{result.error}</div>
          )}
          <ChecksPanel checks={result.checks} />
        </div>
        <div className="flex flex-col gap-4">
          <Panel title="Details">
            <KeyValues
              className="sm:grid-cols-1"
              items={[
                { label: "Check set", value: checkSet(result.check_set_version) },
                { label: "Scenario hash", value: shortHash(result.scenario_hash), mono: true },
                { label: "Tags", value: scenario.data ? <Tags tags={scenario.data.tags} /> : "—" },
                { label: "Latency", value: ms(result.latency_ms) },
                { label: "Claude calls", value: `${result.llm_calls} · ${result.models.join(", ") || "—"}` },
                { label: "Tokens in / out", value: `${count(result.input_tokens)} / ${count(result.output_tokens)}` },
                { label: "Cache write / read", value: `${count(result.cache_creation_input_tokens)} / ${count(result.cache_read_input_tokens)}` },
                { label: "Estimated cost", value: usd(result.estimated_cost_usd) },
                { label: "Judge", value: result.judge_model ? `${result.judge_verdict ?? "—"} (${result.judge_model})` : "off" },
              ]}
            />
          </Panel>
          {result.trace && (
            <Link
              to={`/traces/${result.trace.execution_id}`}
              className="rounded-xl border border-border bg-surface px-4 py-3 text-sm font-medium text-blue-strong shadow-card transition-colors duration-200 hover:bg-blue-soft"
            >
              Open the trace →
            </Link>
          )}
        </div>
      </div>
    </>
  );
}

export function ResultDetail() {
  const params = useParams();
  const runId = Number(params.runId);
  const variantId = params.variantId ?? "";
  const result = useResult(runId, variantId);
  return (
    <>
      <Link to={`/runs/${runId}`} className="mb-3 inline-flex items-center gap-1 text-xs font-medium text-text-muted hover:text-navy">
        <ArrowLeft aria-hidden className="size-3.5" /> Run #{runId}
      </Link>
      <QueryState query={result}>{(r) => <ResultView result={r} />}</QueryState>
    </>
  );
}
