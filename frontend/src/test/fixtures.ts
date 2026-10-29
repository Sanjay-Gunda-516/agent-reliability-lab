// Test-only factories that build valid backend responses. Never imported by the app.
import type {
  AgentVersion,
  Approval,
  ComparisonVariant,
  MetricDelta,
  Rate,
  ReportDetail,
  ReportSummary,
  ResultDetail,
  ResultSummary,
  RunMetrics,
  ScenarioDetail,
  TestRunDetail,
  TestRunListItem,
  TraceDetail,
  TraceEvent,
  TraceSummary,
} from "@/api/types";

const rate = (passed: number, total: number): Rate => ({
  passed,
  total,
  rate: total ? passed / total : null,
});

const tokens = (calls = 14) => ({
  llm_calls: calls,
  input_tokens: 2456,
  output_tokens: 866,
  cache_creation_input_tokens: 8303,
  cache_read_input_tokens: 11137,
});

export function metrics(passed = 3, total = 4, overrides: Partial<RunMetrics> = {}): RunMetrics {
  return {
    variants: total,
    passed,
    failed: total - passed,
    errored: 0,
    task_success: rate(passed, total),
    policy_compliance: rate(3, 4),
    intent_accuracy: rate(4, 4),
    tool_selection_accuracy: rate(2, 2),
    tool_argument_accuracy: rate(2, 2),
    escalation_accuracy: rate(4, 4),
    failure_recovery: rate(0, 0),
    crash_rate: rate(0, total),
    judge_behavior: rate(0, 0),
    judge_grounded: rate(0, 0),
    forbidden_tool_violations: 1,
    missed_escalations: 0,
    unnecessary_escalations: 0,
    failed_checks: { "tools.forbidden": 1 },
    task_success_by_tag: {},
    latency: { mean_ms: 5095.6, p50_ms: 5200, p95_ms: 5855.85, max_ms: 5900 },
    tokens: tokens(),
    judge_tokens: { ...tokens(0), input_tokens: 0, output_tokens: 0, cache_creation_input_tokens: 0, cache_read_input_tokens: 0 },
    tool_calls: 6,
    estimated_cost_usd: "0.091393",
    estimated_judge_cost_usd: null,
    ...overrides,
  };
}

export function run(id: number, overrides: Partial<TestRunListItem> = {}): TestRunListItem {
  return {
    id,
    status: "completed",
    agent_version: "v1",
    judge_model: null,
    check_set_version: "checks-v2",
    variant_count: 4,
    error: null,
    rerun_id: null,
    rerun_side: null,
    repetition: null,
    created_at: `2026-10-29T0${id % 10}:00:00Z`,
    started_at: `2026-10-29T0${id % 10}:00:01Z`,
    finished_at: `2026-10-29T0${id % 10}:00:30Z`,
    metrics: metrics(),
    ...overrides,
  };
}

export function runDetail(id: number, overrides: Partial<TestRunDetail> = {}): TestRunDetail {
  const base = run(id);
  return {
    ...base,
    metrics: base.metrics ?? metrics(),
    agent_config: { model: "claude-opus-5", prompt_version: "v1", effort: "medium" },
    selection: { variant_ids: ["cancel-eligible-order:normal"] },
    results_recorded: 4,
    ...overrides,
  };
}

export function result(variantId: string, overrides: Partial<ResultSummary> = {}): ResultSummary {
  const [scenario = variantId, persona = "normal"] = variantId.split(":");
  return {
    variant_id: variantId,
    scenario_id: scenario,
    persona,
    verdict: "pass",
    check_set_version: "checks-v2",
    critical_failures: 0,
    major_failures: 0,
    minor_failures: 0,
    final_status: "completed",
    escalation_reason: null,
    intent: "cancel_order",
    judge_verdict: null,
    latency_ms: 5200,
    llm_calls: 4,
    tool_calls: 2,
    estimated_cost_usd: "0.024200",
    ...overrides,
  };
}

export function resultDetail(variantId: string, overrides: Partial<ResultDetail> = {}): ResultDetail {
  return {
    ...result(variantId),
    scenario_hash: "43de79b97bd4cb5393689f540e765abf25fc2634f4aa8e4b6da434a86253efc5",
    final_response: "Order #4821 is cancelled.",
    error: null,
    models: ["claude-opus-5"],
    input_tokens: 617,
    output_tokens: 216,
    cache_creation_input_tokens: 307,
    cache_read_input_tokens: 5568,
    tool_errors: 0,
    judge_model: null,
    judge_error: null,
    judge_cost_usd: null,
    started_at: "2026-10-29T01:00:00Z",
    checks: [],
    trace: {
      execution_id: "1ed4c90fa06b4aaf863bac6aaabd71d8",
      turns: [],
      messages: [],
      initial_state: { orders: { "4821": "processing" }, returns: {} },
      final_state: { orders: { "4821": "cancelled" }, returns: {} },
      injected_faults: [],
    },
    ...overrides,
  };
}

export function scenario(id: string, overrides: Partial<ScenarioDetail> = {}): ScenarioDetail {
  return {
    id,
    title: "Cancel an unshipped order",
    tags: ["happy-path"],
    customer_id: 1001,
    expected_outcome: "order_cancelled",
    expected_status: "completed",
    content_hash: "43de79b97bd4cb5393689f540e765abf25fc2634f4aa8e4b6da434a86253efc5",
    is_active: true,
    updated_at: "2026-10-28T00:00:00Z",
    variants: [{ id: `${id}:normal`, persona: "normal", turns: ["Please cancel order #4821."], is_active: true }],
    definition: {
      expected: {
        behavior: "Cancels order 4821 and confirms it.",
        outcome: "order_cancelled",
        final_status: "completed",
        intent: "cancel_order",
        must_call: [{ tool: "cancel_order", args: { order_id: 4821 }, result: "success" }],
        must_not_call: [],
        state: { orders: { "4821": "cancelled" } },
      },
    },
    ...overrides,
  };
}

export function traceSummary(executionId: string, overrides: Partial<TraceSummary> = {}): TraceSummary {
  return {
    execution_id: executionId,
    source: "api",
    request_id: "575765c1b5974311a06713e46cda9ee3",
    test_run_id: null,
    scenario_id: null,
    variant_id: null,
    customer_id: 1001,
    agent_version: "v1",
    model: "claude-opus-5",
    status: "completed",
    escalation_reason: null,
    error: null,
    started_at: "2026-10-29T01:00:00Z",
    finished_at: "2026-10-29T01:00:09Z",
    ...overrides,
  };
}

export function traceDetail(executionId: string, overrides: Partial<TraceDetail> = {}): TraceDetail {
  return {
    ...traceSummary(executionId),
    correlation: {
      execution_id: executionId,
      request_id: "575765c1b5974311a06713e46cda9ee3",
      test_run_id: null,
      evaluation_result_id: null,
      scenario_id: null,
      variant_id: null,
    },
    timing: { total_ms: 9245, model_ms: 8952, tool_ms: 43, evaluation_ms: 0 },
    agent_tokens: { calls: 4, input_tokens: 626, output_tokens: 450, cache_creation_input_tokens: 2418, cache_read_input_tokens: 3994 },
    judge_tokens: { calls: 0, input_tokens: 0, output_tokens: 0, cache_creation_input_tokens: 0, cache_read_input_tokens: 0 },
    counts: { events: 19, turns: 1, tool_calls: 4, retries: 0, errors: 0, rule_rejections: 1 },
    turns: [],
    messages: [],
    initial_state: null,
    final_state: null,
    injected_faults: null,
    ...overrides,
  };
}

let sequence = 0;
export function event(type: string, overrides: Partial<TraceEvent> = {}): TraceEvent {
  sequence += 1;
  return {
    sequence,
    turn: 1,
    type,
    node: null,
    status: "ok",
    started_at: "2026-10-29T01:00:00Z",
    duration_ms: null,
    tool_name: null,
    attempt: null,
    model: null,
    input_tokens: null,
    output_tokens: null,
    cache_creation_input_tokens: null,
    cache_read_input_tokens: null,
    error_type: null,
    error_message: null,
    attributes: {},
    ...overrides,
  };
}

export function approval(id: number, overrides: Partial<Approval> = {}): Approval {
  return {
    id,
    execution_id: "1ed4c90fa06b4aaf863bac6aaabd71d8",
    tool_name: "create_return",
    arguments: { order_id: 7420, reason: "Screen flickers constantly" },
    customer_id: 1001,
    order_id: 7420,
    refund_amount: "1249.00",
    risk_level: "high",
    reason: "Refund of $1249.00 is over the $500.00 limit for automatic refunds.",
    customer_reason: "Screen flickers constantly",
    status: "pending",
    decided_by: null,
    decision_note: null,
    decided_at: null,
    action_status: "not_run",
    action_result: null,
    resumed_at: null,
    created_at: "2026-10-29T01:00:00Z",
    ...overrides,
  };
}

export function version(name: string, overrides: Partial<AgentVersion> = {}): AgentVersion {
  return {
    name,
    description: name === "v1" ? "Claude Opus 5, prompt v1." : "Demo candidate with a deliberately weaker prompt.",
    model: "claude-opus-5",
    prompt_version: name,
    config: { model: "claude-opus-5", prompt_version: name, effort: "medium", max_agent_turns: 8 },
    config_hash: `${name}hash0000000000000000000000000000000000000000000000000000000`.slice(0, 64),
    defined_in_code: true,
    created_at: "2026-10-28T00:00:00Z",
    ...overrides,
  };
}

export function reportSummary(id: number, overrides: Partial<ReportSummary> = {}): ReportSummary {
  return {
    id,
    revision: 1,
    root_report_id: null,
    rerun_id: null,
    baseline_run_id: 4,
    candidate_run_id: 5,
    baseline_agent_version: "v1",
    candidate_agent_version: "v1-regressed",
    baseline_check_set_versions: ["checks-v2"],
    candidate_check_set_versions: ["checks-v2"],
    gate_status: "fail",
    counts: { regression: 1, unchanged: 3 },
    created_at: "2026-10-29T02:00:00Z",
    ...overrides,
  };
}

export function comparisonVariant(variantId: string, overrides: Partial<ComparisonVariant> = {}): ComparisonVariant {
  return {
    variant_id: variantId,
    scenario_id: variantId.split(":")[0] ?? variantId,
    category: "unchanged",
    gated: true,
    needs_rerun: false,
    baseline_result_id: 1,
    candidate_result_id: 2,
    baseline_verdict: "pass",
    candidate_verdict: "pass",
    baseline_scenario_hash: "43de79b9",
    candidate_scenario_hash: "43de79b9",
    baseline_check_set_version: "checks-v2",
    candidate_check_set_version: "checks-v2",
    new_critical_failures: [],
    resolved_critical_failures: [],
    new_failed_checks: [],
    fixed_checks: [],
    latency_delta_ms: -100,
    cost_delta_usd: "-0.002000",
    explanation: "pass in both runs",
    rerun_evidence: null,
    ...overrides,
  };
}

export const taskSuccessDelta: MetricDelta = {
  name: "task_success",
  unit: "pp",
  better: "higher",
  baseline: 75,
  candidate: 50,
  delta: -25,
  change_pct: null,
  baseline_detail: "3/4",
  candidate_detail: "2/4",
};

export function reportDetail(id: number, overrides: Partial<ReportDetail> = {}): ReportDetail {
  return {
    ...reportSummary(id),
    thresholds: {
      fail_on_new_critical: true,
      max_task_success_drop_pp: "5",
      max_cost_increase_pct: "25",
      max_latency_increase_pct: "25",
    },
    gate: {
      status: "fail",
      reasons: [
        {
          code: "task_success_drop",
          level: "fail",
          message: "Task success fell 25.00 points (3/4 → 2/4), more than the 5-point limit.",
          details: {},
        },
        {
          code: "variant_regressions",
          level: "warn",
          message: "1 variant(s) got worse without failing a new critical check.",
          details: {},
        },
      ],
    },
    metric_deltas: [
      taskSuccessDelta,
      {
        name: "estimated_cost_usd",
        unit: "usd",
        better: "lower",
        baseline: 0.091393,
        candidate: 0.078175,
        delta: -0.013218,
        change_pct: -14.46,
        baseline_detail: null,
        candidate_detail: null,
      },
    ],
    baseline_metrics: metrics(3, 4),
    candidate_metrics: metrics(2, 4),
    variants: [comparisonVariant("cancel-shipped-order:normal")],
    revisions: [id],
    ...overrides,
  };
}
