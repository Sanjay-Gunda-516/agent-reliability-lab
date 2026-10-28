# Observability

How to follow one request, agent execution, tool call or evaluation through logs,
stored traces and (optionally) LangSmith.

## Correlation

One mechanism everywhere: a `Correlation` (request id, test run id, execution id,
scenario id, variant id) held in a Python context variable. Each entry point adds its
ids, and everything running inside inherits them, including work FastAPI runs in its
threadpool:

| Entry point | Adds |
|---|---|
| HTTP middleware | `request_id`: the caller's `X-Request-ID` if it's a sane token, otherwise a new one; echoed back in the response header |
| Test run execution | `test_run_id` |
| Scenario variant | `scenario_id`, `variant_id` |
| Agent execution (`run_agent`) | `execution_id` |

`execution_id` is the key that ties everything together. It's the public id of the
`execution_traces` row, is returned by `POST /agent/run`, is stamped on every log line
and LangSmith run, and in Phase 7 becomes the LangGraph thread id, so a conversation
paused for human approval resumes in the same trace.

## Structured logging

- `LOG_FORMAT=text` (default): `12:00:01 INFO app.trace: tool_call [execution_id=… tool=get_order status=ok duration_ms=4.2]`
- `LOG_FORMAT=json`: one JSON object per line for CloudWatch and CI, with `timestamp`,
  `level`, `logger`, `message`, the correlation ids and any event fields.

Ids are captured when a log record is created, not when it's formatted, so buffered or
asynchronous handlers keep them.

**Never logged:** customer message text (only its length), API keys, tokens or
passwords. A redaction pass masks Anthropic and LangSmith keys, bearer tokens, passwords
in connection URLs and `key=`/`password=` pairs in messages, extra fields and tracebacks.
Levels: `INFO` for normal steps, `WARNING` for retries, failed steps and fallbacks,
`ERROR` for failed requests and crashes.

## Trace model

```
execution_traces   one per agent execution (a live chat or a scenario variant)
├── trace_events   the timeline: one row per step, in start order
└── tool_calls     one row per tool invocation, normalized for SQL
evaluation_results ─> execution_traces   (a result points to the run it graded)
```

**Events.** Timed steps are spans (`started_at` and `duration_ms`); instantaneous
things are point events (no duration).

| Type | Kind | Records |
|---|---|---|
| `request_received` | point | message length, history length |
| `intent` | span | model, tokens, intent label |
| `model_call` | span | model, tokens, stop reason; `error` on outage, refusal or truncation |
| `tool_selected` | point | tool name, tool_use id |
| `tool_call` | span | tool, attempts; status `ok`, `rejected` (business rule) or `error` |
| `tool_retry` | point | tool, attempt number, error type and message (inside its `tool_call`) |
| `rule_rejection` | point | tool, error code and rule reason (e.g. `already_shipped`) |
| `escalation` | point | escalation reason |
| `response_finalized` | point | final status, reply length |
| `evaluation` | span | verdict, critical and major failure counts |
| `judge` | span | judge model, tokens, behaviour verdict, unsupported-claim count |

Each event has `sequence` (order within the execution), `turn`, `node`, `status`, and
optional `tool_name`, `attempt`, `model`, token counts, `error_type`/`error_message`
(redacted, max 500 characters) and `attributes` (a small JSON object).

**Tool calls** (`tool_calls`): tool name, arguments, status, error code, attempts,
latency, output and start time, linked to their execution and turn. Calls recorded
before tracing existed were copied out of the old per-turn JSON by the migration (their
start time is unknown).

## Query API

| Endpoint | Returns |
|---|---|
| `GET /traces?source=&test_run_id=&status=&limit=` | trace summaries, newest first |
| `GET /traces/{execution_id}` | correlation ids, timing (total, Claude, tools, evaluation), agent and judge tokens, counts (events, turns, tool calls, retries, errors, rule rejections), turns, messages, states, faults |
| `GET /traces/{execution_id}/events?type=&status=` | the timeline, optionally filtered |
| `GET /traces/{execution_id}/tool-calls` | normalized tool calls |

`POST /agent/run` returns `execution_id` and `request_id`; evaluation result details
include their trace's `execution_id`.

## LangSmith (optional)

Off unless `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` are both set in
`backend/.env`. Tracing is switched on or off explicitly around every run, so a stray
environment variable can't enable it. When on, each agent execution appears as a
`support-agent` run with one child per graph node and the Claude calls (with token
usage) nested inside; every run carries the correlation ids as metadata. Set
`LANGSMITH_HIDE_CONTENT=true` to send structure and timing without prompts or replies.
Our own tables remain the system of record; LangSmith is a convenience for browsing.

## Overhead

Measured with `scripts/benchmark_tracing.py`: the real graph, tools and Postgres, with
Claude scripted, 300 interleaved runs per mode after warm-up, log lines formatted as JSON
into memory. It's a lookup turn with 7 trace events, measured on a development laptop
across two runs:

| | Median per run |
|---|---|
| Agent run with tracing off (no-op recorder) | 2.0–2.3 ms |
| Agent run with tracing on | 2.4–2.9 ms |
| Recording overhead | 0.4–0.6 ms |
| Saving the trace (events and tool calls, one flush) | 2.1–2.4 ms |

The offline run has no model latency, so the relative overhead looks large there
(about 20–25%). In a real run, where Claude calls dominate (the live sample averaged
5.9 s per variant), it's well under a hundredth of a percent. All tests passed
unchanged with tracing switched on.
