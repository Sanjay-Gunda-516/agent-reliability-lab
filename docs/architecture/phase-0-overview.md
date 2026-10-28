# Agent Reliability Lab — Phase 0: Product Definition & Architecture

Status: Draft — pending final review. No application code exists yet —
this doc is the scaffolding everything else gets built on.

---

## 1. What this project is

**Agent Reliability Lab** tests an AI customer-support agent before it
ships, the same way a CI suite protects normal application code — except
tuned for how AI agents actually break (wrong tool calls, made-up facts,
policy violations, latency/cost blowing up, regressions after you touch a
prompt or swap a model).

It's really two things working together:

1. **The agent under test** — a LangGraph-orchestrated ecommerce
   support agent with real tools (order lookup, cancellation, returns,
   refund-eligibility checks) sitting on top of a Postgres-backed mock
   store.
2. **The reliability lab itself** — a FastAPI service plus a React
   dashboard that defines test scenarios with known-correct answers, runs
   them against the agent, scores the results with deterministic checks
   (and a bit of LLM-as-judge where nothing else works), stores the full
   execution trace of every run, and flags regressions between agent
   versions.

---

## 2. The problem

Normal software has a clean pass/fail contract: input X should produce
output Y, exactly. Agents don't work like that — the same request can be
phrased ten different ways, and "correct" is more about policy compliance
and safe tool use than matching a string. Teams shipping LLM agents into
support flows mostly have no automated safety net against:

- the agent picking the wrong tool, or the right tool with wrong arguments
- the agent inventing information it never actually looked up
- the agent following an unsafe instruction hidden in the user's text
  (prompt injection)
- a prompt or model change quietly making things worse on cases that used
  to work fine (regression)
- the agent getting slow or expensive in ways nobody notices until the
  bill shows up

That's the gap this project is closing: make these failure modes visible,
measurable, and something CI can actually block on — the same discipline
unit tests already give regular code, adapted for a system that isn't
deterministic.

---

## 3. Who this is for

It's a portfolio project, so "users" here just stand in for the real roles
a system like this would serve:

- **An AI/ML engineer** who wants a pre-deploy gate that catches
  regressions before a prompt change hits real customers.
- **A QA/reliability engineer** who writes test scenarios — including
  adversarial and edge cases — the same way they'd write test plans for
  any other system, without having to touch agent code.
- **An engineering manager** who wants a dashboard that answers "is it
  safe to ship version B instead of version A?" without reading raw
  transcripts.
- **A human reviewer** who only gets pulled in for the cases the agent
  itself flags as risky (human-in-the-loop approval).

---

## 4. MVP user stories

- As an engineer, I can define a **test scenario** — input text plus the
  expected intent, tool, arguments, and outcome — without touching agent
  code.
- As an engineer, I can attach **persona variants** (normal, angry,
  incomplete, adversarial) to a scenario so the same underlying case gets
  tested under different phrasing.
- As an engineer, I can **run a scenario suite** against a specific agent
  version and get pass/fail plus scores back per scenario.
- As an engineer, I can **inspect the execution trace** of any run — which
  node ran, which tool was called with what arguments, what came back, how
  long it took, how many tokens it burned.
- As an engineer, I can **compare two agent versions** (say, prompt v1 vs.
  v2) and see whether task success, tool accuracy, policy compliance,
  latency, or cost moved — and whether that move crosses a regression
  threshold.
- As a (simulated) customer, I can ask the agent to cancel or return an
  order, or ask about one, and get a response that's correct — and that
  never claims success when the underlying action actually failed.
- As a human approver, I can see a queue of agent recommendations flagged
  high-risk (a large refund, say), approve or reject them, and have the
  agent pick back up with my decision.
- As a CI pipeline, I can run a subset of the eval suite on every pull
  request and fail the build if reliability metrics drop past a configured
  threshold.

---

## 5. Functional requirements

- A deterministic mock ecommerce domain: customers, orders, order line
  items, order status lifecycle.
- A LangGraph agent that can understand intent, look up customer/order
  data, apply business rules, pick and call tools, validate what the tools
  return, assess risk, escalate to a human when needed, and generate a
  final response.
- A scenario format (input, persona, ground truth) that's storable and
  versioned independently of agent code.
- A test-execution engine that runs scenarios against a chosen agent
  version and records the full trace.
- Deterministic evaluators for intent accuracy, tool-selection accuracy,
  tool-argument accuracy, business-rule/policy compliance, task success,
  API-failure recovery, and human-escalation correctness.
- A narrow LLM-judge evaluator for the one thing that can't be checked
  deterministically — e.g. "did the response state something factually
  unsupported?"
- Regression comparison between two agent versions, with configurable pass
  thresholds.
- A human-approval workflow that can pause an agent run and resume it
  later.
- A REST API exposing all of the above.
- A React dashboard: overview metrics, scenario management, run
  triggering, results, trace viewer, version comparison, approval queue.

## 5a. Non-functional requirements

- **Correctness over cleverness** — business rules (cancellation
  eligibility, refund limits, cross-customer access) live in deterministic
  code, never in LLM judgment. More on this in Section 6.
- **Reproducibility** — a run has to be re-runnable, and its trace has to
  actually explain *why* the agent did what it did, not leave you
  guessing.
- **Loop safety** — the graph has a hard step limit. No scenario, however
  adversarial, gets to hang forever or run up an unbounded bill.
- **Cost awareness** — ordinary unit/integration tests never call a real
  LLM. Live-model evaluation is its own, separately-triggered suite.
- **Auditability** — every tool call, human decision, and evaluation
  result is stored with enough metadata to reconstruct what happened,
  without storing secrets or PII we don't need.
- **Portability** — the whole stack comes up locally with one Docker
  Compose command, no cloud account required.

---

## 6. High-level architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                      React + TS (Vite SPA)                          │
└──────────────────────────────┬───────────────────────────────────────┘
                                │ HTTPS / JSON (REST)
                                ▼
┌────────────────────────────────────────────────────────────────────┐
│                              FastAPI                                 │
│      scenarios / test-run / approvals / comparisons /                │
│      agent-invocation APIs                                           │
└───────────────┬──────────────────────────────────┬───────────────────┘
                │                                  │
   PART 2 — RELIABILITY LAB                PART 1 — AGENT UNDER TEST
   (scenario, run & eval logic,             (one call per agent
    via SQLAlchemy)                          invocation)
                │                                  │
                │                                  ▼
                │                     ┌───────────────────────────┐
                │            ┌────────│          LangGraph          │
                │            │        │  understand_intent →        │
                │       checkpoint     │  retrieve_* → choose_tool → │
                │       save/load      │  execute_tool → validate →  │
                │      (pause/resume,  │  assess_risk → respond /    │
                │       Section 12)    │  escalate                   │
                │            │         └──────┬───────────────┬──────┘
                │            │                │               │
                │            │   reasoning / tool-choice   tool calls
                │            │                │               │
                │            │                ▼               ▼
                │            │   ┌─────────────────────┐  ┌───────────────────────────┐
                │            │   │  Anthropic Claude API │  │  Domain "tools" layer      │
                │            │   │  (reasoning +         │  │  (get_order, cancel_order, │
                │            │   │   tool-calling only — │  │   check_refund_eligibility,│
                │            │   │   never touches the   │  │   create_return, ...)      │
                │            │   │   database)           │  │   via SQLAlchemy           │
                │            │   └─────────────────────┘  └─────────────┬─────────────┘
                │            │                                           │
                ▼            ▼                                           ▼
┌────────────────────────────────────────────────────────────────────┐
│                              PostgreSQL                              │
│  Part 1 tables (via SQLAlchemy): customers, orders                   │
│  Part 2 tables (via SQLAlchemy): test_scenarios, scenario_variants,  │
│  agent_versions, test_runs, evaluation_results, execution_traces,    │
│  tool_calls, human_approvals, regression_reports                     │
│  LangGraph checkpoint tables (LangGraph's own Postgres saver):       │
│  paused/interrupted run state, not part of our SQLAlchemy schema     │
└────────────────────────────────────────────────────────────────────┘
```

Two things this version of the diagram fixes over an earlier draft: the
Claude API is only ever called *by LangGraph*, for reasoning and tool
selection — it never sits between the tools layer and the database. And
the domain "tools" layer is the only thing that reads or writes
ecommerce data; LangGraph never touches customer/order rows directly, it
goes through tools.

That said, there are really **three separate paths into the same
Postgres instance**, and it's worth being precise about which code talks
to which tables:

1. **The domain "tools" layer** (Part 1) accesses `customers`/`orders`
   through SQLAlchemy — this is the validated, deterministic path the
   agent's tool calls go through.
2. **The Reliability Lab's own backend logic** (Part 2, inside FastAPI)
   also goes through SQLAlchemy, but against its own tables —
   `test_scenarios`, `test_runs`, `evaluation_results`,
   `execution_traces`, `tool_calls`, and the rest — to define scenarios,
   record runs, and store evaluation results and traces.
3. **LangGraph's own persistent checkpointer** writes workflow state
   directly to Postgres, using its own checkpoint tables — separate from
   our SQLAlchemy models entirely. This is what lets a paused
   human-in-the-loop run be picked back up later, possibly by a different
   process, as described in Section 12.

All three share one physical database for simplicity, but they're three
distinct clients writing three distinct sets of tables — not one shared
code path.

The diagram also splits the system into its two halves, matching Section
1: **Part 1** is the agent under test (LangGraph, the tools layer, Claude),
and **Part 2** is the reliability lab itself (FastAPI's scenario/run/eval
logic, writing straight to its own tables). Both halves share one
Postgres database, but Part 2 never reasons about orders directly — when
it needs the agent to act, it goes through Part 1 via `agent-invocation`,
the same as any other caller would.

The rule this diagram is built around: the LLM never talks to the database
directly. It reasons and picks a tool call; a plain, deterministic Python
tools layer (via SQLAlchemy) is what actually enforces business rules and
touches Postgres. Below is that split spelled out.

### LLM reasoning vs. business rules vs. tool execution

- **LLM reasoning** — "What does the customer want? Which tool fits? How do
  I phrase the answer?" Fuzzy, language-shaped problems. This is the only
  layer where it's fine for the answer to vary.
- **Business rules** — "Is this order still cancellable? Does this refund
  need a human to sign off? Does this customer even own this order?"
  These are facts, computed the same way every time, in plain Python (e.g.
  `order.status == "shipped"` → not cancellable). The LLM gets told the
  outcome; it doesn't compute it.
- **Tool execution** — the actual mutation (`cancel_order(order_id)`) is a
  function call with a fixed schema, validated inputs, and a real return
  value that the graph checks (`validate_tool_result`) before the LLM is
  allowed to tell the customer anything succeeded.

If the LLM decided cancellation eligibility from its own reasoning, two
customers asking the exact same thing in different words could get
different answers. Pushing that decision into deterministic code is what
makes the whole system testable — which is the point of this project.

---

## 7. Request flow — worked example

Input: `"Cancel order #4821."`

```
1. React POSTs { message: "Cancel order #4821." } to
   POST /agents/{agent_version_id}/run
2. FastAPI creates a run record, invokes the LangGraph agent with initial
   state { user_message, customer_id, conversation_history: [] }
3. understand_intent (LLM call)
      → { intent: "cancel_order", extracted_order_id: 4821 }
4. retrieve_order(4821)                          [tool: get_order]
      → order found, status = "processing", belongs to this customer
5. assess_risk / check business rule
      → status == "processing" → cancellation IS allowed, low risk
      → (if it had been "shipped", the graph would route to a
         "cannot cancel, offer return" branch instead — see Section 12)
6. choose_tool → cancel_order
7. execute_tool(cancel_order, order_id=4821)
      → { success: true, new_status: "cancelled" }
8. validate_tool_result → success confirmed against the tool's own return
   value (never assumed)
9. generate_response (LLM call, given the confirmed facts only)
      → "Order #4821 has been cancelled."
10. FastAPI persists all of it under one execution_traces row for this
    run: steps 3, 5, 6, 8, and 9 each become a trace event (node name,
    timestamp, latency, tokens), step 7 also lands in tool_calls, and the
    final response goes back to React.
```

This run produces exactly one `execution_traces` row — not one row per
step. Every numbered line above becomes a trace event *belonging to*
that one row, and step 7 additionally gets its own `tool_calls` row
(Section 10) since it was a tool invocation specifically. This worked
example is basically the trace viewer's contract, written out by hand.

---

## 8. Technology stack, and why

| Layer | Choice | Why |
|---|---|---|
| LLM | Anthropic Claude, via the Anthropic API | Solid native tool-calling and structured output; sticking to one provider keeps the tool-schema and prompt work concrete instead of hedged. Exact model version gets picked in Phase 3, not locked in here. |
| Orchestration | LangGraph | There's a real need for stateful, branching, resumable workflow (Section 12) — not using it just because it looks good on a resume. |
| Backend framework | FastAPI | Async-native, Pydantic validation built in, auto-generated OpenAPI docs — all of that is directly useful for an API shaped like this one. |
| Validation/schemas | Pydantic | The same models double as request/response schemas and as the argument schemas the LLM's tool calls get validated against. |
| ORM | SQLAlchemy + Alembic | An explicit relational schema (Section 10) with real foreign keys; Alembic carries migrations from local dev straight through to the RDS deploy in Phase 12. |
| Database | PostgreSQL | Relational integrity actually matters here — an order belongs to exactly one customer, a tool call belongs to exactly one trace. No need for a document store, and no need for vector search (Section 9). |
| Frontend | React + TypeScript + Vite | This is an internal dashboard, not a marketing site — no SSR/SEO need, so a plain Vite SPA is simpler than Next.js for the same portfolio credibility. |
| Testing | Pytest | Unit, integration, and agent-eval tests all live in one framework; mocking the LLM in ordinary tests keeps the suite fast and free (Section 5a). |
| Observability | Structured logging + LangSmith | LangSmith gives agent-level trace visualization pretty much for free; structured logs cover the FastAPI/DB layer. OpenTelemetry is deferred (Section 9). |
| Containerization | Docker + Docker Compose | One command brings up Postgres and the backend locally; the same backend image is what ships to ECS later. |
| CI/CD | GitHub Actions | Standard, free on a public repo, and it's the direct demonstration of "tests gate the merge" this whole project is arguing for. |
| Deployment | AWS Amplify (frontend) + Amazon ECS Express Mode (backend container) + Amazon RDS PostgreSQL + CloudWatch | See Section 9 for why App Runner got replaced with ECS Express Mode, and why this still beats hand-rolled ECS-on-EC2 or Kubernetes for a solo project on a 2–4 week timeline. |

---

## 9. What we're deliberately not using

- **pgvector / vector search** — this is structured business data (orders,
  statuses, refund amounts), not semantic document retrieval. Adding a
  vector index here would just be padding the stack with a keyword.
- **Redis / a message queue** — nothing in the MVP needs background job
  processing or a cache layer. An ordinary agent request — no human
  approval involved — finishes inside a normal HTTP timeout, start to
  end, in one request/response cycle. The one exception is a
  human-approval workflow: that pauses via LangGraph's persistent
  checkpointer and picks back up later through a separate follow-up
  request (Section 12), rather than holding the original request open or
  needing a queue to bridge the gap.
- **OpenTelemetry, for now** — it's genuinely useful once you have several
  services and need distributed request-level tracing, but this MVP is one
  backend service. LangSmith (agent-level) plus structured logs
  (app-level) already cover what we need to debug. It's a documented
  future improvement, not something skipped out of ignorance.
- **A multi-provider LLM abstraction (OpenAI + Anthropic)** — a real
  feature in a system that has to stay provider-portable, but here it'd be
  indirection with nothing on the other end to actually switch to. One
  provider keeps the tool-calling and prompt work concrete.
- **Kubernetes** — that solves a scaling problem (many services, complex
  rollouts) that a one-service, solo-built project just doesn't have.
- **AWS App Runner** — as of 2026 it's no longer available to new AWS
  customers. **Amazon ECS Express Mode** is AWS's current recommended
  replacement, and it gives the same "point at a container image, get a
  running service" simplicity App Runner used to, without having to
  hand-manage a cluster, load balancer, or EC2 fleet the way "classic"
  ECS-on-EC2 would.

---

## 10. Initial database design (entity level, not SQLAlchemy yet)

This is deliberately kept at the entity/relationship level. Column-level
SQLAlchemy models, indexes, and constraints are Phase 1 work — writing
those now, before the underlying concepts (primary key, foreign key,
index) have actually been introduced, would be getting ahead of the
learning plan for this project.

```
customers ──1───────*── orders ──1───────*── human_approvals
   (the mock ecommerce domain; a human_approvals row is a paused,
    high-risk action on one specific order)

agent_versions ──1───────*── test_runs

test_scenarios ──1───────*── scenario_variants (persona rewordings)

test_runs ──*───────*── test_scenarios
   (a run executes a chosen set of scenarios/variants; this many-to-many
    is realized one row at a time below, per scenario actually executed)

test_runs ──1───────*── execution_traces ──*───1── test_scenarios
   (exactly one execution_trace row per scenario run within this
    test_run, holding the ordered sequence of trace events — one per
    workflow step — that make up what the agent actually did)

execution_traces ──1───────*── tool_calls
   (tool_calls are the subset of that execution_trace's trace events
    that were tool invocations specifically — get_order, cancel_order,
    etc.; an order_id shows up here as a tool call's argument, not as a
    formal foreign key, since some scenarios deliberately reference an
    order that doesn't exist)

execution_traces ──1───────1── evaluation_results
   (the scored outcome for that same scenario-execution: the trace is
    what happened, the evaluation_result is how it scored against the
    scenario's ground truth — see Section 13)

test_runs ──1───────1── regression_reports (when run compared against a baseline)
```

**What each table is for:**

- `customers`, `orders` — the mock ecommerce domain the agent operates on.
- `agent_versions` — a named, versioned config (prompt + model + graph
  revision) — the unit regression testing actually compares.
- `test_scenarios` — the input plus the ground truth (expected intent,
  tool, outcome) for one testable case.
- `scenario_variants` — persona rewordings (angry, incomplete, adversarial)
  of the same scenario, so one ground truth covers a bunch of phrasings.
- `test_runs` — one execution of a scenario set against one agent version.
- `execution_traces` — one row per scenario execution within a run,
  holding the ordered sequence of trace events (one per workflow step —
  `understand_intent`, `retrieve_order`, `assess_risk`, `execute_tool`,
  and so on) recorded during it. This is what the trace viewer is built
  from, and what `evaluation_results` and `tool_calls` both hang off of.
- `evaluation_results` — the scored outcome of one scenario-execution
  (pass/fail, per-metric scores, failure category), scored against that
  scenario's own ground truth — not by re-running the agent's internal
  business-rule code (see Section 13).
- `tool_calls` — one recorded tool invocation (name, arguments, result,
  latency), belonging to the execution_trace it happened during.
- `human_approvals` — a paused-for-review decision point: recommendation,
  risk level, what the reviewer decided, when.
- `regression_reports` — the computed diff between a run and its baseline,
  plus the pass/fail regression verdict.

**What's deliberately not stored**: raw LLM API keys, more customer PII
than a scenario actually needs (name/email is enough — no payment data),
and raw prompt/response blobs once a trace has already been evaluated
(we keep what's useful for debugging, not everything).

Indexing, uniqueness constraints, and cascade behavior get decided once
the actual SQLAlchemy models are written in Phase 1 — they depend on query
patterns we haven't exercised yet, so deciding them now would be guessing.

---

## 11. Initial API design (a sketch, not final)

```
POST   /agents                              create an agent version (prompt/model/graph config)
GET    /agents/{id}                         fetch one agent version
POST   /agents/{id}/run                     invoke the agent for a single message (used by manual testing + scenario runner)

POST   /scenarios                           create a test scenario (+ ground truth)
GET    /scenarios                           list scenarios
GET    /scenarios/{id}                      fetch one scenario incl. variants

POST   /test-runs                           start a run: { agent_version_id, scenario_ids[] }
GET    /test-runs/{id}                      run status/summary
GET    /test-runs/{id}/results              per-scenario pass/fail + scores
GET    /test-runs/{id}/traces               execution traces for the run

GET    /comparisons/{version_a}/{version_b} regression comparison between two versions

GET    /approvals                           pending human-approval queue
POST   /approvals/{id}/approve              resume a paused agent run with an approval
POST   /approvals/{id}/reject               resume a paused agent run with a rejection
```

A couple of resource-model notes: `agents` and `scenarios` are independent
of each other — neither owns the other. A `test_run` is really the join
between "which agent version" and "which scenarios," and everything else
(`results`, `traces`) nests underneath the run that produced it.
`approvals` gets its own resource because it needs to be listed/queried
across runs for the approval-queue view. Standard REST status codes apply
— 404 for a missing agent/scenario/run, 422 for Pydantic validation
errors, 409 if you try to approve something already resolved. Full
request/response schemas come in Phase 1/7, once the underlying models
exist.

---

## 12. LangGraph workflow design (cancellation path)

```
                         START
                           │
                    understand_intent
                           │
                     retrieve_customer
                           │
                      retrieve_order
                           │
                 ┌─────────┴─────────┐
             order found?        order not found
                 │                    │
                 ▼                    ▼
          check_order_status    clarification /
                 │              failure response
      ┌──────────┼──────────┐        │
   not shipped  shipped   ambiguous  │
      │            │          │      │
      ▼            ▼          ▼      │
 assess_risk   return_flow  ask_for_ │
      │        (suggest      clarification
      │         return,          │
      │         no cancel)       │
   ┌──┴──┐         │             │
 low   high        │             │
risk   risk        │             │
   │     │         │             │
   │     ▼         │             │
   │ request_human_│             │
   │  approval     │             │
   │     │         │             │
   │  (approved?)  │             │
   │   yes │ no    │             │
   ▼   ▼   ▼       │             │
 choose_tool  escalate           │
   │   (cancel      │            │
   │    denied)     │            │
   ▼        │       │            │
execute_tool│       │            │
   │        │       │            │
validate_   │       │            │
tool_result │       │            │
   │        │       │            │
   └────┬───┴───┬───┴────────────┘
        ▼
  generate_response
        │
       END
```

We're only talking through this conceptually right now — it becomes actual
Python (`StateGraph`, nodes, conditional edges) in Phase 3, where
LangGraph's vocabulary (state, node, edge, conditional edge, checkpointing,
interrupt) gets taught against this exact diagram, term by term. Two
things worth calling out already:

- **Loop protection** — the graph carries a `step_count` in its state and a
  conditional edge that forces it to stop past a fixed max. No scenario —
  including adversarial ones built to confuse intent detection — can loop
  forever.
- **Human-in-the-loop** lives inside this same graph, not bolted on
  separately — a LangGraph interrupt at `request_human_approval` actually
  pauses execution. The tricky part: the approval might come back seconds
  later, or hours later, in a completely separate HTTP request (`POST
  /approvals/{id}/approve`) that could even hit a different backend
  process than the one that started the run. FastAPI itself is stateless
  between requests, so the paused graph's state has to be written
  somewhere durable — a checkpointer backed by Postgres, not just held in
  memory — so that whichever process handles the approval call can load
  the exact point the run paused at and continue from there. That
  persistent checkpointing is what makes pausing "across requests" actually
  work, rather than just pausing for the duration of one function call
  (a Phase 7 topic).

---

## 13. Evaluation architecture

Two kinds of evaluator, each doing a different job on purpose:

**Deterministic evaluators** (preferred everywhere they'll work — cheap,
exact, reproducible):
- Intent accuracy: did `understand_intent`'s output match the ground truth?
- Tool-selection accuracy: was the right tool called — or correctly *not*
  called, e.g. `cancel_order` should never fire on a shipped order?
- Tool-argument accuracy: exact match against what was expected.
- Business-rule/policy compliance: checked against the scenario's own
  ground truth — the expected outcome an engineer wrote down when the
  scenario was authored — not by re-invoking the same business-rule
  functions the agent calls internally. If the evaluator just called the
  agent's own rule code and compared the answer to itself, a bug in that
  rule (say, a cancellation-window check that's off by a day) would look
  "correct" to the evaluator too, because it's the exact same bug grading
  its own homework. Ground truth has to be authored independently of the
  implementation so it can actually catch that case.
- Task success: did the final system state (order status in Postgres, say)
  match what was expected?
- API-failure recovery: given an injected tool failure, did the agent
  handle it — retry, or an explicit error — instead of claiming it worked?
- Human-escalation correctness: did a high-risk case actually pause for
  approval, and a low-risk one not?

**LLM-as-a-judge** (used narrowly, only where nothing deterministic will
do the job):
- Hallucination / unsupported-claim detection in the final response — e.g.
  did it invent a policy that doesn't exist?

**Why the judge stays narrow, and where it falls short**: an LLM judge is
itself a non-deterministic model grading another model's output. It can
be inconsistent run to run, it tends to reward answers that *sound* right
over ones that *are* right, and it's slower and more expensive than a rule
check. It's used here for exactly one thing this project has no other way
to check — open-ended factual or policy claims in prose — and every metric
that can be computed deterministically is computed that way instead.

Any number that ends up in the README, the dashboard, or a resume bullet
comes from actually running this suite. Nothing here is a placeholder.

---

## 14. Repository / folder structure

```
agent-reliability-lab/
├── backend/
│   ├── app/
│   │   ├── api/              # FastAPI routers (agents, scenarios, runs, approvals)
│   │   ├── domain/            # customers/orders models + business rules (pure Python)
│   │   ├── tools/              # agent tool implementations (get_order, cancel_order, ...)
│   │   ├── agent/              # LangGraph state, nodes, graph definition
│   │   ├── evaluation/         # deterministic evaluators + LLM judge
│   │   ├── db/                 # SQLAlchemy models, session, Alembic env
│   │   └── main.py
│   ├── alembic/                # migrations
│   ├── tests/
│   │   ├── unit/
│   │   ├── integration/
│   │   └── evaluation/
│   └── .env.example            # backend + local Postgres config (copy to backend/.env)
├── frontend/
│   └── src/
│       ├── pages/               # Overview, Scenarios, Runs, Results, Traces, Comparison, Approvals
│       └── api/                 # typed API client
├── docker/
│   └── backend.Dockerfile
├── docker-compose.yml           # at the root so `docker compose up` works from the repo
├── .github/workflows/           # CI: unit/integration tests, eval gate
├── docs/
│   └── architecture/            # this document and future phase notes
├── .gitignore
└── README.md
```

`domain/` (business rules) is kept separate from `agent/` (LangGraph
orchestration) and `tools/` (the surface between them) — that's the
LLM-reasoning/business-rules/tool-execution split from Section 6, made
concrete in the folder layout. `evaluation/` sits apart from `tests/`
because evaluators are product code the lab runs at runtime, while
`tests/` is what verifies that product code actually works.

---

## 15. Development roadmap

1. **Phase 0 — Product & architecture** *(this document)*
2. **Phase 1 — Backend foundation**: FastAPI app, config, Postgres,
   SQLAlchemy, Alembic, health endpoint, first tests.
3. **Phase 2 — Ecommerce domain**: customers/orders models, business rules,
   mock tools, seed data, unit tests.
4. **Phase 3 — LangGraph agent**: state, nodes, tools wired in, conditional
   routing, loop protection, error handling, agent-invocation endpoint.
5. **Phase 4 — Scenario framework**: scenario/ground-truth schemas,
   persona variants, execution engine.
6. **Phase 5 — Evaluation engine**: deterministic evaluators, narrow LLM
   judge, metrics, failure classification.
7. **Phase 6 — Tracing/observability**: execution traces, structured
   logging, LangSmith integration.
8. **Phase 7 — Human-in-the-loop**: risk classification, LangGraph
   interrupt + checkpointing, approval API.
9. **Phase 8 — Regression testing**: agent versions, baselines, comparison
   engine, regression report.
10. **Phase 9 — Frontend**: overview, scenarios, runs, results, trace
    viewer, comparison, approval queue.
11. **Phase 10 — Docker**: Dockerfiles, Compose, one-command local env.
12. **Phase 11 — CI/CD**: GitHub Actions — unit/integration tests, eval
    quality gate.
13. **Phase 12 — Deployment**: Amplify (frontend), ECS Express Mode
    (backend), RDS Postgres, CloudWatch.
14. **Phase 13 — Evaluation & optimization**: real benchmark run, failure
    analysis, prompt/graph improvements, before/after comparison.
15. **Phase 14 — Portfolio polish**: README, architecture diagrams,
    screenshots, resume bullets, interview prep.

---

## 16. Main technical risks

- **LLM non-determinism in tests** — handled by mocking the LLM in
  unit/integration tests and treating live-model evaluation as its own,
  separately-triggered and smaller suite (Section 5a/Phase 5).
- **Scope creep** — the original brief lists a lot of "nice to have" items
  (multi-provider support, deep OpenTelemetry, advanced analytics).
  Deliberately deferred — see Section 9 and the roadmap.
- **Graph runaway / infinite loops** — handled by a hard step-count limit
  in agent state (Section 12).
- **Evaluation validity** — a deterministic evaluator is only as good as
  its ground truth, so scenario ground truth needs actual review, not
  auto-generation.
- **Cost during development** — real Anthropic API calls cost money;
  bounded by mocking in most tests and only running live evals on purpose.

## 17. Main trade-offs

- Single-provider LLM (simpler, more concrete) vs. multi-provider
  portability (not needed at this scale).
- Vite SPA (simple, fast dev loop) vs. Next.js (SSR/SEO features this
  internal dashboard has no use for).
- ECS Express Mode (managed, simple) vs. raw ECS-on-EC2/Kubernetes (more
  control, way more operational surface than a solo project needs).
- LangSmith + structured logs (fast to get real value from) vs. full
  OpenTelemetry (more standardized, more setup, worth it once there's a
  second service to trace across).

## 18. What to learn before Phase 1

- Enough Python async to read FastAPI's `async def` endpoints without it
  being a surprise — not a deep dive, just enough to follow along.
- What an ORM is conceptually — an SQLAlchemy model maps to a table row.
  Real depth comes once we write the models in Phase 1.
- Basic Pydantic syntax (`BaseModel`, field types) — this shows up
  constantly from Phase 1 on.

## 19. Interview concepts from Phase 0

- What an AI agent is, and how it's different from a plain LLM call.
- Why LangGraph specifically — stateful, branching, resumable — and not
  just a name on a resume.
- The LLM-reasoning vs. business-rules vs. tool-execution split, and why
  the LLM never writes to the database directly.
- Deterministic evaluators vs. LLM-as-a-judge, and why the judge stays
  narrow.
- Why regression testing matters specifically for AI systems — a prompt
  change can quietly make things worse with no compiler around to catch
  it.
- Why App Runner got swapped for ECS Express Mode in the deployment
  plan — a real example of an architecture decision reacting to a
  platform change instead of being copied from an old tutorial.

---

## Learning log (started here, grows every phase)

**Product/architecture (Phase 0)**
- The difference between an agent's *reasoning* and a system's *business
  rules* — and why mixing the two makes a system impossible to test
  properly.
- Why a stack should be justified against the actual problem, not chosen
  for resume density. pgvector, Redis, Kubernetes, OpenTelemetry, and a
  multi-provider LLM layer were all considered and deliberately left out
  here — see Section 9.
- What deterministic evaluation vs. LLM-as-a-judge actually means, and why
  most of an agent's behavior should be checked deterministically instead
  of by asking another model.

## Interview knowledge list (started here, grows every phase)

- What is an AI agent, and how is it different from "an app that calls an
  LLM"?
- Why does this project use LangGraph specifically?
- Why do business rules (like cancellation eligibility) have to live in
  deterministic code instead of LLM judgment?
- What's the difference between a deterministic evaluator and an
  LLM-as-a-judge evaluator, and when does each make sense?
- Why is regression testing necessary for AI agents specifically?
