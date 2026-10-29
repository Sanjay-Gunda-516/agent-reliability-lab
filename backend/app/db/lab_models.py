"""Reliability Lab tables (Part 2). The store tables (Part 1) live in models.py."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    true,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ScenarioRecord(Base):
    """A scenario synced from backend/scenarios/*.yaml (the files are the source of truth)."""

    __tablename__ = "test_scenarios"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(30)))
    customer_id: Mapped[int]
    expected_outcome: Mapped[str] = mapped_column(String(40))
    expected_status: Mapped[str] = mapped_column(String(20))
    # The full validated definition. Always read as a whole, never queried into.
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    variants: Mapped[list["ScenarioVariantRecord"]] = relationship(
        back_populates="scenario", order_by="ScenarioVariantRecord.id"
    )


class ScenarioVariantRecord(Base):
    __tablename__ = "scenario_variants"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)  # "<scenario>:<persona>"
    scenario_id: Mapped[str] = mapped_column(ForeignKey("test_scenarios.id"), index=True)
    persona: Mapped[str] = mapped_column(String(20))
    turns: Mapped[list[str]] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())

    scenario: Mapped[ScenarioRecord] = relationship(back_populates="variants")


class AgentVersionRecord(Base):
    """A named agent configuration (see app/agent/versions.py), recorded when first used."""

    __tablename__ = "agent_versions"

    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    description: Mapped[str] = mapped_column(Text)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # Config plus the prompt texts it selects; a name never changes meaning.
    config_hash: Mapped[str] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(50))
    prompt_version: Mapped[str | None] = mapped_column(String(50))
    # False for versions only known from old runs (e.g. "default" from before Phase 8).
    defined_in_code: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TestRunRecord(Base):
    """One execution of a selection of scenario variants against one agent configuration."""

    __tablename__ = "test_runs"
    __test__ = False  # not a pytest test class

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(20))  # queued | running | completed | failed
    agent_version: Mapped[str] = mapped_column(ForeignKey("agent_versions.name"), index=True)
    agent_config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    judge_model: Mapped[str | None] = mapped_column(String(50))
    selection: Mapped[dict[str, Any]] = mapped_column(JSONB)
    variant_count: Mapped[int]
    # Snapshot computed from evaluation_results when the run finishes.
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(64))  # the request that started it
    # Set when the run repeats variants for a regression rerun.
    rerun_id: Mapped[int | None] = mapped_column(ForeignKey("regression_reruns.id"), index=True)
    rerun_side: Mapped[str | None] = mapped_column(String(10))  # baseline | candidate
    repetition: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    results: Mapped[list["EvaluationResultRecord"]] = relationship(
        back_populates="test_run", order_by="EvaluationResultRecord.id"
    )


class EvaluationResultRecord(Base):
    """How one scenario variant scored within a test run."""

    __tablename__ = "evaluation_results"
    __table_args__ = (UniqueConstraint("test_run_id", "variant_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    test_run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id"), index=True)
    execution_trace_id: Mapped[int] = mapped_column(ForeignKey("execution_traces.id"), unique=True)
    scenario_id: Mapped[str] = mapped_column(ForeignKey("test_scenarios.id"), index=True)
    variant_id: Mapped[str] = mapped_column(ForeignKey("scenario_variants.id"))
    persona: Mapped[str] = mapped_column(String(20))
    scenario_hash: Mapped[str] = mapped_column(String(64))
    verdict: Mapped[str] = mapped_column(String(10))  # pass | fail | error
    critical_failures: Mapped[int]
    major_failures: Mapped[int]
    minor_failures: Mapped[int]
    final_status: Mapped[str | None] = mapped_column(String(20))
    escalation_reason: Mapped[str | None] = mapped_column(String(40))
    intent: Mapped[str | None] = mapped_column(String(40))
    final_response: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    models: Mapped[list[str]] = mapped_column(ARRAY(String(50)))  # can differ after a fallback
    latency_ms: Mapped[float]
    llm_calls: Mapped[int]
    input_tokens: Mapped[int]
    output_tokens: Mapped[int]
    cache_creation_input_tokens: Mapped[int]
    cache_read_input_tokens: Mapped[int]
    tool_calls: Mapped[int]
    tool_errors: Mapped[int]
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    judge_model: Mapped[str | None] = mapped_column(String(50))
    judge_verdict: Mapped[str | None] = mapped_column(String(10))
    judge_error: Mapped[str | None] = mapped_column(Text)
    judge_usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    judge_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    test_run: Mapped[TestRunRecord] = relationship(back_populates="results")
    scenario: Mapped[ScenarioRecord] = relationship()
    checks: Mapped[list["EvaluationCheckRecord"]] = relationship(
        back_populates="result", order_by="EvaluationCheckRecord.id"
    )
    trace: Mapped["ExecutionTraceRecord"] = relationship(back_populates="result")


class EvaluationCheckRecord(Base):
    """One check of one result. A table rather than JSON so failures can be grouped in SQL."""

    __tablename__ = "evaluation_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    evaluation_result_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_results.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(50), index=True)
    target: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(20))  # deterministic | judge
    severity: Mapped[str] = mapped_column(String(10))
    passed: Mapped[bool]
    reason: Mapped[str] = mapped_column(Text)
    expected: Mapped[Any] = mapped_column(JSONB, nullable=True)
    actual: Mapped[Any] = mapped_column(JSONB, nullable=True)

    result: Mapped[EvaluationResultRecord] = relationship(back_populates="checks")


class ExecutionTraceRecord(Base):
    """One agent execution (a live chat or a scenario variant) and everything it did.

    `execution_id` is the public correlation key: it appears in logs, LangSmith metadata
    and API responses, and in Phase 7 becomes the LangGraph thread id.
    """

    __tablename__ = "execution_traces"

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(32), unique=True)
    source: Mapped[str] = mapped_column(String(20))  # api | evaluation
    request_id: Mapped[str | None] = mapped_column(String(64), index=True)
    test_run_id: Mapped[int | None] = mapped_column(ForeignKey("test_runs.id"), index=True)
    scenario_id: Mapped[str | None] = mapped_column(String(80))
    variant_id: Mapped[str | None] = mapped_column(String(120))
    customer_id: Mapped[int]
    agent_version: Mapped[str | None] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20))  # completed | escalated | error
    escalation_reason: Mapped[str | None] = mapped_column(String(40))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Per-turn summary and the conversation; tool and Claude calls live in their own rows.
    turns: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    # Scenario runs only.
    initial_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    final_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    injected_faults: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    result: Mapped[EvaluationResultRecord | None] = relationship(back_populates="trace")
    events: Mapped[list["TraceEventRecord"]] = relationship(
        back_populates="trace", order_by="TraceEventRecord.sequence"
    )
    tool_calls: Mapped[list["ToolCallRow"]] = relationship(
        back_populates="trace", order_by="ToolCallRow.sequence"
    )


class TraceEventRecord(Base):
    """One step of an execution: a span (started_at + duration) or a point event."""

    __tablename__ = "trace_events"
    __table_args__ = (UniqueConstraint("execution_trace_id", "sequence"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_trace_id: Mapped[int] = mapped_column(ForeignKey("execution_traces.id"), index=True)
    sequence: Mapped[int]
    turn: Mapped[int]
    type: Mapped[str] = mapped_column(String(30), index=True)
    node: Mapped[str | None] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(10))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[float | None]
    tool_name: Mapped[str | None] = mapped_column(String(50))
    attempt: Mapped[int | None]
    model: Mapped[str | None] = mapped_column(String(50))
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    cache_creation_input_tokens: Mapped[int | None]
    cache_read_input_tokens: Mapped[int | None]
    error_type: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB)

    trace: Mapped[ExecutionTraceRecord] = relationship(back_populates="events")


class ToolCallRow(Base):
    """One tool invocation, normalized for SQL analysis (named to avoid clashing with the
    agent's in-memory ToolCallRecord)."""

    __tablename__ = "tool_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_trace_id: Mapped[int] = mapped_column(ForeignKey("execution_traces.id"), index=True)
    sequence: Mapped[int]  # order within the execution
    turn: Mapped[int]
    tool_use_id: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(50), index=True)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(10), index=True)  # success | rejected | error
    error: Mapped[str | None] = mapped_column(String(50))
    attempts: Mapped[int]
    latency_ms: Mapped[float]
    output: Mapped[Any] = mapped_column(JSONB, nullable=True)
    # Unknown for calls migrated from before tracing existed.
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    trace: Mapped[ExecutionTraceRecord] = relationship(back_populates="tool_calls")


class HumanApprovalRecord(Base):
    """A high-risk action paused for a person to approve or reject.

    `status` is the decision (pending -> approved | rejected); `action_status` tracks the
    approved action itself (not_run -> executing -> succeeded | failed), which is what
    guarantees it runs at most once.
    """

    __tablename__ = "human_approvals"
    __table_args__ = (UniqueConstraint("execution_id", "tool_use_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(32), index=True)
    tool_use_id: Mapped[str] = mapped_column(String(100))
    tool_name: Mapped[str] = mapped_column(String(50))
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB)
    customer_id: Mapped[int]
    # Not a foreign key: the demo store is reset by seeding, approvals are history.
    order_id: Mapped[int | None]
    refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    risk_level: Mapped[str] = mapped_column(String(10))
    reason: Mapped[str] = mapped_column(Text)  # why the system needs a human
    customer_reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), index=True)
    decided_by: Mapped[str | None] = mapped_column(String(100))
    decision_note: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    action_status: Mapped[str] = mapped_column(String(10))
    action_result: Mapped[Any] = mapped_column(JSONB, nullable=True)
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RegressionReportRecord(Base):
    """A comparison of a candidate test run against a baseline run, with its gate verdict.

    Reports are never edited. Rerunning flipped variants creates a new revision whose
    `root_report_id` points at the original comparison.
    """

    __tablename__ = "regression_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    baseline_run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id"), index=True)
    candidate_run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id"), index=True)
    baseline_agent_version: Mapped[str] = mapped_column(String(50))
    candidate_agent_version: Mapped[str] = mapped_column(String(50))
    revision: Mapped[int]  # 1 for the original comparison
    root_report_id: Mapped[int | None] = mapped_column(ForeignKey("regression_reports.id"))
    rerun_id: Mapped[int | None] = mapped_column(ForeignKey("regression_reruns.id"))
    thresholds: Mapped[dict[str, Any]] = mapped_column(JSONB)
    gate_status: Mapped[str] = mapped_column(String(10))  # pass | warn | fail
    gate: Mapped[dict[str, Any]] = mapped_column(JSONB)  # reasons, machine-readable
    counts: Mapped[dict[str, int]] = mapped_column(JSONB)  # variants per category
    metric_deltas: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    baseline_metrics: Mapped[dict[str, Any]] = mapped_column(JSONB)
    candidate_metrics: Mapped[dict[str, Any]] = mapped_column(JSONB)
    request_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    variants: Mapped[list["RegressionVariantRecord"]] = relationship(
        back_populates="report", order_by="RegressionVariantRecord.id"
    )


class RegressionVariantRecord(Base):
    """How one scenario variant compared within a report."""

    __tablename__ = "regression_variant_results"
    __table_args__ = (UniqueConstraint("report_id", "variant_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("regression_reports.id"), index=True)
    variant_id: Mapped[str] = mapped_column(String(120))
    scenario_id: Mapped[str] = mapped_column(String(80))
    # regression | confirmed_regression | improvement | unchanged | noisy | scenario_changed
    # | missing_in_candidate | new_in_candidate
    category: Mapped[str] = mapped_column(String(30), index=True)
    gated: Mapped[bool]  # counted by the gate (paired, same scenario content, not noisy)
    needs_rerun: Mapped[bool]
    baseline_result_id: Mapped[int | None] = mapped_column(ForeignKey("evaluation_results.id"))
    candidate_result_id: Mapped[int | None] = mapped_column(ForeignKey("evaluation_results.id"))
    baseline_verdict: Mapped[str | None] = mapped_column(String(10))
    candidate_verdict: Mapped[str | None] = mapped_column(String(10))
    baseline_scenario_hash: Mapped[str | None] = mapped_column(String(64))
    candidate_scenario_hash: Mapped[str | None] = mapped_column(String(64))
    new_critical_failures: Mapped[list[str]] = mapped_column(JSONB)
    resolved_critical_failures: Mapped[list[str]] = mapped_column(JSONB)
    new_failed_checks: Mapped[list[str]] = mapped_column(JSONB)
    fixed_checks: Mapped[list[str]] = mapped_column(JSONB)
    latency_delta_ms: Mapped[float | None]
    cost_delta_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    explanation: Mapped[str] = mapped_column(Text)
    rerun_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    report: Mapped[RegressionReportRecord] = relationship(back_populates="variants")


class RegressionRerunRecord(Base):
    """A request to repeat flipped variants k times, to tell noise from real regressions."""

    __tablename__ = "regression_reruns"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(
        ForeignKey("regression_reports.id", use_alter=True), index=True
    )  # always the original (revision 1) report
    repetitions: Mapped[int] = mapped_column(Integer)
    sides: Mapped[str] = mapped_column(String(10))  # candidate | both
    variant_ids: Mapped[list[str]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20))  # queued | running | completed | failed
    error: Mapped[str | None] = mapped_column(Text)
    result_report_id: Mapped[int | None] = mapped_column(
        ForeignKey("regression_reports.id", use_alter=True)
    )
    request_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
