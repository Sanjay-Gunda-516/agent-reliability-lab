// Response shapes of the FastAPI backend (see /openapi.json). Decimals arrive as strings.

export type Verdict = "pass" | "fail" | "error";
export type Severity = "critical" | "major" | "minor";
export type RunStatus = "queued" | "running" | "completed" | "failed";

export interface Rate {
  passed: number;
  total: number;
  rate: number | null;
}

export interface LatencyStats {
  mean_ms: number | null;
  p50_ms: number | null;
  p95_ms: number | null;
  max_ms: number | null;
}

export interface TokenTotals {
  llm_calls: number;
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
}

export interface RunMetrics {
  variants: number;
  passed: number;
  failed: number;
  errored: number;
  task_success: Rate;
  policy_compliance: Rate;
  intent_accuracy: Rate;
  tool_selection_accuracy: Rate;
  tool_argument_accuracy: Rate;
  escalation_accuracy: Rate;
  failure_recovery: Rate;
  crash_rate: Rate;
  judge_behavior: Rate;
  judge_grounded: Rate;
  forbidden_tool_violations: number;
  missed_escalations: number;
  unnecessary_escalations: number;
  failed_checks: Record<string, number>;
  task_success_by_tag: Record<string, Rate>;
  latency: LatencyStats;
  tokens: TokenTotals;
  judge_tokens: TokenTotals;
  tool_calls: number;
  estimated_cost_usd: string | null;
  estimated_judge_cost_usd: string | null;
}

export interface TestRunSummary {
  id: number;
  status: RunStatus;
  agent_version: string;
  judge_model: string | null;
  check_set_version: string | null;
  variant_count: number;
  error: string | null;
  rerun_id: number | null;
  rerun_side: string | null;
  repetition: number | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface TestRunListItem extends TestRunSummary {
  metrics: RunMetrics | null;
}

export interface TestRunDetail extends TestRunSummary {
  agent_config: Record<string, unknown>;
  selection: Record<string, unknown>;
  results_recorded: number;
  metrics: RunMetrics;
}

export interface ResultSummary {
  variant_id: string;
  scenario_id: string;
  persona: string;
  verdict: Verdict;
  check_set_version: string | null;
  critical_failures: number;
  major_failures: number;
  minor_failures: number;
  final_status: string | null;
  escalation_reason: string | null;
  intent: string | null;
  judge_verdict: string | null;
  latency_ms: number;
  llm_calls: number;
  tool_calls: number;
  estimated_cost_usd: string | null;
}

export interface CheckView {
  name: string;
  target: string | null;
  source: "deterministic" | "judge";
  severity: Severity;
  passed: boolean;
  reason: string;
  expected: unknown;
  actual: unknown;
}

export interface ResultTrace {
  execution_id: string;
  turns: Record<string, unknown>[];
  messages: Record<string, unknown>[];
  initial_state: Record<string, unknown> | null;
  final_state: Record<string, unknown> | null;
  injected_faults: Record<string, unknown>[];
}

export interface ResultDetail extends ResultSummary {
  scenario_hash: string;
  final_response: string | null;
  error: string | null;
  models: string[];
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
  tool_errors: number;
  judge_model: string | null;
  judge_error: string | null;
  judge_cost_usd: string | null;
  started_at: string;
  checks: CheckView[];
  trace: ResultTrace | null;
}

export interface FailedCheck extends CheckView {
  variant_id: string;
  scenario_id: string;
}

export interface ScenarioVariant {
  id: string;
  persona: string;
  turns: string[];
  is_active: boolean;
}

export interface ScenarioSummary {
  id: string;
  title: string;
  tags: string[];
  customer_id: number;
  expected_outcome: string;
  expected_status: string;
  content_hash: string;
  is_active: boolean;
  updated_at: string;
  variants: ScenarioVariant[];
}

export interface ToolExpectation {
  tool: string;
  args: Record<string, unknown> | null;
  result: string;
}

export interface ScenarioDefinition {
  expected: {
    behavior: string;
    outcome: string;
    final_status: string;
    escalation_reason?: string | null;
    intent?: string | null;
    must_call?: ToolExpectation[];
    must_not_call?: ToolExpectation[];
    state?: Record<string, unknown>;
    response?: Record<string, unknown> | null;
  };
  faults?: Record<string, unknown>[];
  [key: string]: unknown;
}

export interface ScenarioDetail extends ScenarioSummary {
  definition: ScenarioDefinition;
}

export interface TraceSummary {
  execution_id: string;
  source: "api" | "evaluation";
  request_id: string | null;
  test_run_id: number | null;
  scenario_id: string | null;
  variant_id: string | null;
  customer_id: number;
  agent_version: string | null;
  model: string;
  status: string;
  escalation_reason: string | null;
  error: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface TraceDetail extends TraceSummary {
  correlation: {
    execution_id: string;
    request_id: string | null;
    test_run_id: number | null;
    evaluation_result_id: number | null;
    scenario_id: string | null;
    variant_id: string | null;
  };
  timing: { total_ms: number | null; model_ms: number; tool_ms: number; evaluation_ms: number };
  agent_tokens: TraceTokens;
  judge_tokens: TraceTokens;
  counts: {
    events: number;
    turns: number;
    tool_calls: number;
    retries: number;
    errors: number;
    rule_rejections: number;
  };
  turns: Record<string, unknown>[];
  messages: Record<string, unknown>[];
  initial_state: Record<string, unknown> | null;
  final_state: Record<string, unknown> | null;
  injected_faults: Record<string, unknown>[] | null;
}

export interface TraceTokens {
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
}

export interface TraceEvent {
  sequence: number;
  turn: number;
  type: string;
  node: string | null;
  status: "ok" | "rejected" | "retry" | "error";
  started_at: string;
  duration_ms: number | null;
  tool_name: string | null;
  attempt: number | null;
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cache_creation_input_tokens: number | null;
  cache_read_input_tokens: number | null;
  error_type: string | null;
  error_message: string | null;
  attributes: Record<string, unknown>;
}

export interface ToolCallView {
  sequence: number;
  turn: number;
  tool_use_id: string;
  name: string;
  arguments: Record<string, unknown>;
  status: "success" | "rejected" | "error";
  error: string | null;
  attempts: number;
  latency_ms: number;
  output: unknown;
  started_at: string | null;
}

export interface Approval {
  id: number;
  execution_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  customer_id: number;
  order_id: number | null;
  refund_amount: string | null;
  risk_level: string;
  reason: string;
  customer_reason: string | null;
  status: "pending" | "approved" | "rejected";
  decided_by: string | null;
  decision_note: string | null;
  decided_at: string | null;
  action_status: "not_run" | "executing" | "succeeded" | "failed";
  action_result: unknown;
  resumed_at: string | null;
  created_at: string;
}

export interface DecisionResponse {
  approval: Approval;
  execution_id: string;
  execution_status: string;
  final_response: string;
}

export interface AgentVersion {
  name: string;
  description: string;
  model: string | null;
  prompt_version: string | null;
  config: Record<string, unknown>;
  config_hash: string;
  defined_in_code: boolean;
  created_at: string;
}

export type Category =
  | "regression"
  | "confirmed_regression"
  | "improvement"
  | "unchanged"
  | "noisy"
  | "scenario_changed"
  | "checks_changed"
  | "missing_in_candidate"
  | "new_in_candidate";

export type GateStatus = "pass" | "warn" | "fail";

export interface GateReason {
  code: string;
  level: "fail" | "warn";
  message: string;
  details: Record<string, unknown>;
}

export interface GateResult {
  status: GateStatus;
  reasons: GateReason[];
}

export interface GateThresholds {
  fail_on_new_critical: boolean;
  max_task_success_drop_pp: string;
  max_cost_increase_pct: string;
  max_latency_increase_pct: string;
}

export interface MetricDelta {
  name: string;
  unit: "pp" | "ms" | "usd" | "count";
  better: "higher" | "lower";
  baseline: number | null;
  candidate: number | null;
  delta: number | null;
  change_pct: number | null;
  baseline_detail: string | null;
  candidate_detail: string | null;
}

export interface ReportSummary {
  id: number;
  revision: number;
  root_report_id: number | null;
  rerun_id: number | null;
  baseline_run_id: number;
  candidate_run_id: number;
  baseline_agent_version: string;
  candidate_agent_version: string;
  baseline_check_set_versions: (string | null)[];
  candidate_check_set_versions: (string | null)[];
  gate_status: GateStatus;
  counts: Partial<Record<Category, number>>;
  created_at: string;
}

export interface Observation {
  test_run_id: number | null;
  result_id: number | null;
  verdict: Verdict;
  critical_failures: string[];
  scenario_hash: string;
  check_set_version?: string | null;
  counted: boolean;
  reproduces: boolean | null;
}

export interface RerunEvidence {
  first_category: Category;
  outcome: Category;
  candidate: Observation[];
  baseline: Observation[];
  candidate_reproduced: number;
  candidate_counted: number;
  baseline_reproduced: number;
  baseline_counted: number;
  explanation: string;
}

export interface ComparisonVariant {
  variant_id: string;
  scenario_id: string;
  category: Category;
  gated: boolean;
  needs_rerun: boolean;
  baseline_result_id: number | null;
  candidate_result_id: number | null;
  baseline_verdict: Verdict | null;
  candidate_verdict: Verdict | null;
  baseline_scenario_hash: string | null;
  candidate_scenario_hash: string | null;
  baseline_check_set_version: string | null;
  candidate_check_set_version: string | null;
  new_critical_failures: string[];
  resolved_critical_failures: string[];
  new_failed_checks: string[];
  fixed_checks: string[];
  latency_delta_ms: number | null;
  cost_delta_usd: string | null;
  explanation: string;
  rerun_evidence: RerunEvidence | null;
}

export interface ReportDetail extends ReportSummary {
  thresholds: GateThresholds;
  gate: GateResult;
  metric_deltas: MetricDelta[];
  baseline_metrics: RunMetrics;
  candidate_metrics: RunMetrics;
  variants: ComparisonVariant[];
  revisions: number[];
}

export interface Rerun {
  id: number;
  report_id: number;
  repetitions: number;
  sides: string;
  variant_ids: string[];
  status: RunStatus;
  error: string | null;
  result_report_id: number | null;
  created_at: string;
  finished_at: string | null;
  test_run_ids: number[];
  estimated_cost_usd: string | null;
}
