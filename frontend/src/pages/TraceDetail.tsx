import { ArrowLeft } from "lucide-react";
import { Fragment, useState } from "react";
import { Link, useParams } from "react-router";

import { useTrace, useTraceEvents, useTraceToolCalls } from "@/api/queries";
import type { ToolCallView, TraceDetail as Trace, TraceEvent } from "@/api/types";
import { KeyValues, Mono, RawDetails, Value } from "@/components/common/Details";
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
import { count, dateTime, ms } from "@/lib/format";
import { isNoteworthy, presentEvent } from "@/lib/events";
import { executionStyle, eventStatusStyle, TONE_CLASSES, TONE_TEXT } from "@/lib/status";
import { cn } from "@/lib/utils";

function TimelineItem({ event }: { event: TraceEvent }) {
  const p = presentEvent(event);
  const Icon = p.icon;
  return (
    <li className="relative flex gap-3 pb-4 last:pb-0" data-event-type={event.type}>
      <span
        className={cn(
          "relative z-10 flex size-8 shrink-0 items-center justify-center rounded-full border",
          TONE_CLASSES[p.tone],
          p.minor && "size-6 mx-1",
        )}
      >
        <Icon aria-hidden className={p.minor ? "size-3" : "size-4"} />
      </span>
      <div
        className={cn(
          "min-w-0 flex-1",
          !p.minor && "rounded-lg border border-border bg-surface px-3 py-2 shadow-card",
        )}
      >
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className={cn("text-sm font-medium", p.minor ? "text-text-muted" : TONE_TEXT[p.tone] === "text-blue-strong" ? "text-navy" : TONE_TEXT[p.tone])}>
            {p.title}
          </span>
          {!p.minor && event.status !== "ok" && <StatusBadge style={eventStatusStyle(event.status)} />}
          <span className="ml-auto flex items-center gap-2 text-xs text-text-muted tabular-nums">
            {event.duration_ms !== null && <span>{ms(event.duration_ms)}</span>}
            <span aria-label={`step ${event.sequence}`}>#{event.sequence}</span>
          </span>
        </div>
        {p.facts.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1.5">
            {p.facts.map((f) => (
              <span key={f} className="rounded-md bg-surface-muted px-1.5 py-0.5 text-xs text-text-muted">
                {f}
              </span>
            ))}
          </div>
        )}
        {event.error_message && (
          <p className="mt-1.5 text-xs text-critical-strong">
            {event.error_type ? `${event.error_type}: ` : ""}
            {event.error_message}
          </p>
        )}
      </div>
    </li>
  );
}

function Divider({ children }: { children: React.ReactNode }) {
  return (
    <li className="relative z-10 my-2 flex items-center gap-3 pl-11 text-xs font-medium text-text-muted">
      <span className="h-px flex-1 bg-border" />
      {children}
      <span className="h-px flex-1 bg-border" />
    </li>
  );
}

export function Timeline({ events }: { events: TraceEvent[] }) {
  const [onlyNoteworthy, setOnlyNoteworthy] = useState(false);
  const shown = onlyNoteworthy ? events.filter(isNoteworthy) : events;
  const turns = new Set(events.map((e) => e.turn)).size;
  return (
    <Panel
      title="Timeline"
      description={`${events.length} steps${turns > 1 ? ` across ${turns} turns` : ""}, in order`}
      action={
        <label className="flex items-center gap-2 text-xs text-text-muted">
          <input type="checkbox" className="size-3.5 accent-[var(--blue)]" checked={onlyNoteworthy} onChange={(e) => setOnlyNoteworthy(e.target.checked)} />
          Only warnings and errors
        </label>
      }
    >
      {shown.length === 0 ? (
        <EmptyState message={onlyNoteworthy ? "No warnings or errors in this execution." : "No events recorded."} />
      ) : (
        <ol aria-label="Execution timeline" className="relative before:absolute before:top-2 before:bottom-2 before:left-4 before:w-px before:bg-border">
          {shown.map((e, i) => {
            const previous = shown[i - 1];
            return (
              <Fragment key={e.sequence}>
                {turns > 1 && previous?.turn !== e.turn && <Divider>Turn {e.turn}</Divider>}
                {e.type === "execution_resumed" && <Divider>Later request · after the decision</Divider>}
                <TimelineItem event={e} />
              </Fragment>
            );
          })}
        </ol>
      )}
    </Panel>
  );
}

function ToolCalls({ calls }: { calls: ToolCallView[] }) {
  if (!calls.length) return <EmptyState message="No tool calls in this execution." />;
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>#</TableHead>
          <TableHead>Tool</TableHead>
          <TableHead>Arguments</TableHead>
          <TableHead>Result</TableHead>
          <TableHead className="text-right">Attempts</TableHead>
          <TableHead className="text-right">Latency</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {calls.map((c) => (
          <TableRow key={`${c.sequence}-${c.tool_use_id}`}>
            <TableCell className="text-text-muted tabular-nums">{c.sequence}</TableCell>
            <TableCell><Mono>{c.name}</Mono></TableCell>
            <TableCell className="max-w-xs truncate text-xs"><Value value={c.arguments} /></TableCell>
            <TableCell>
              <span className={cn("text-xs font-medium", c.status === "success" ? "text-success-strong" : c.status === "rejected" ? "text-warning-strong" : "text-critical-strong")}>
                {c.status}
                {c.error ? ` · ${c.error}` : ""}
              </span>
            </TableCell>
            <TableCell className="text-right tabular-nums">{c.attempts}</TableCell>
            <TableCell className="text-right tabular-nums">{ms(c.latency_ms)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function TraceView({ trace, executionId }: { trace: Trace; executionId: string }) {
  const events = useTraceEvents(executionId);
  const calls = useTraceToolCalls(executionId);
  const c = trace.correlation;
  return (
    <>
      <PageHeader
        title={trace.variant_id ?? `Live chat · customer ${trace.customer_id}`}
        description={`${trace.source === "api" ? "Live chat" : "Evaluation"} · ${trace.agent_version ?? "unknown version"} · ${trace.model} · ${dateTime(trace.started_at)}`}
        actions={<StatusBadge style={executionStyle(trace.status)} />}
      />
      {trace.error && (
        <div role="alert" className="mb-4 rounded-lg border border-critical/25 bg-critical-soft px-4 py-3 text-sm text-critical-strong">{trace.error}</div>
      )}
      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <KpiCard label="Total time" value={ms(trace.timing.total_ms)} detail={`model ${ms(trace.timing.model_ms)} · tools ${ms(trace.timing.tool_ms)}`} />
        <KpiCard label="Claude calls" value={trace.agent_tokens.calls} detail={`${count(trace.agent_tokens.input_tokens)} in · ${count(trace.agent_tokens.output_tokens)} out`} />
        <KpiCard label="Tool calls" value={trace.counts.tool_calls} detail={`${trace.counts.retries} retries · ${trace.counts.rule_rejections} rule rejections`} />
        <KpiCard label="Errors" value={trace.counts.errors} detail={`${trace.counts.events} steps · ${trace.counts.turns} turn${trace.counts.turns === 1 ? "" : "s"}`} />
      </div>
      <div className="grid gap-4 xl:grid-cols-[1fr_22rem]">
        <div className="flex min-w-0 flex-col gap-4">
          <QueryState query={events} loadingRows={6}>{(data) => <Timeline events={data} />}</QueryState>
          <Panel title="Tool calls" flush>
            <QueryState query={calls} padded>{(data) => <ToolCalls calls={data} />}</QueryState>
          </Panel>
        </div>
        <div className="flex flex-col gap-4">
          <Panel title="Correlation ids">
            <KeyValues
              className="sm:grid-cols-1"
              items={[
                { label: "Execution id", value: c.execution_id, mono: true },
                { label: "Request id (first request)", value: c.request_id ?? "—", mono: true },
                {
                  label: "Test run",
                  value: c.test_run_id !== null ? <Link className="text-blue-strong hover:underline" to={`/runs/${c.test_run_id}`}>#{c.test_run_id}</Link> : "—",
                },
                {
                  label: "Scenario result",
                  value:
                    c.test_run_id !== null && c.variant_id ? (
                      <Link className="text-blue-strong hover:underline" to={`/runs/${c.test_run_id}/results/${encodeURIComponent(c.variant_id)}`}>{c.variant_id}</Link>
                    ) : (
                      "—"
                    ),
                },
              ]}
            />
          </Panel>
          <Panel title="Tokens">
            <KeyValues
              items={[
                { label: "Input", value: count(trace.agent_tokens.input_tokens) },
                { label: "Output", value: count(trace.agent_tokens.output_tokens) },
                { label: "Cache write", value: count(trace.agent_tokens.cache_creation_input_tokens) },
                { label: "Cache read", value: count(trace.agent_tokens.cache_read_input_tokens) },
              ]}
            />
            {trace.judge_tokens.calls > 0 && (
              <p className="mt-3 text-xs text-text-muted">
                Judge: {trace.judge_tokens.calls} call · {count(trace.judge_tokens.input_tokens)} in · {count(trace.judge_tokens.output_tokens)} out
              </p>
            )}
          </Panel>
          <Panel title="Raw data" description="Collapsed by default">
            <div className="flex flex-col gap-2">
              <RawDetails label="Conversation messages" value={trace.messages} />
              <RawDetails label="Turns" value={trace.turns} />
              {trace.final_state && <RawDetails label="Final state" value={trace.final_state} />}
              {events.data && <RawDetails label="All events" value={events.data} />}
            </div>
          </Panel>
        </div>
      </div>
    </>
  );
}

export function TraceDetail() {
  const executionId = useParams().executionId ?? "";
  const trace = useTrace(executionId);
  return (
    <>
      <Link to="/traces" className="mb-3 inline-flex items-center gap-1 text-xs font-medium text-text-muted hover:text-navy">
        <ArrowLeft aria-hidden className="size-3.5" /> Traces
      </Link>
      <QueryState query={trace}>{(t) => <TraceView trace={t} executionId={executionId} />}</QueryState>
    </>
  );
}
