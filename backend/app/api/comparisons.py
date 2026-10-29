"""Agent versions and regression comparisons between test runs."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.llm import LLMClient
from app.agent.versions import AgentVersionConflict, sync_agent_versions
from app.api.agent import get_llm_client
from app.db.lab_models import (
    AgentVersionRecord,
    RegressionReportRecord,
    RegressionRerunRecord,
    RegressionVariantRecord,
)
from app.db.session import DbSession, get_sessionmaker
from app.evaluation.comparisons import (
    MAX_REPETITIONS,
    ComparisonError,
    Sides,
    create_comparison,
    estimate_rerun_cost,
    execute_rerun,
    render_report,
    request_rerun,
    rerun_runs,
    revisions,
)
from app.evaluation.regression import Category, GateResult, GateThresholds, MetricDelta
from app.evaluation.service import SelectionError

versions_router = APIRouter(prefix="/agent-versions", tags=["agent versions"])
router = APIRouter(prefix="/comparisons", tags=["comparisons"])

_ERROR_STATUS = {
    "not_found": status.HTTP_404_NOT_FOUND,
    "not_ready": status.HTTP_409_CONFLICT,
    "incompatible": status.HTTP_422_UNPROCESSABLE_CONTENT,
}


class AgentVersionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    description: str
    model: str | None
    prompt_version: str | None
    config: dict[str, Any]
    config_hash: str
    defined_in_code: bool
    created_at: datetime


def _synced(db: Session) -> list[AgentVersionRecord]:
    try:
        records = sync_agent_versions(db)
    except AgentVersionConflict as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    db.commit()
    return records


@versions_router.get("", response_model=list[AgentVersionView])
def list_versions(db: DbSession) -> list[AgentVersionRecord]:
    """Versions defined in code (recorded on first sight) and any known only from old runs."""
    return _synced(db)


@versions_router.get("/{name}", response_model=AgentVersionView)
def get_version(name: str, db: DbSession) -> AgentVersionRecord:
    for record in _synced(db):
        if record.name == name:
            return record
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent version not found.")


class CreateComparisonRequest(BaseModel):
    baseline_run_id: int
    candidate_run_id: int
    thresholds: GateThresholds = Field(default_factory=GateThresholds)


class ReportSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    revision: int
    root_report_id: int | None
    rerun_id: int | None
    baseline_run_id: int
    candidate_run_id: int
    baseline_agent_version: str
    candidate_agent_version: str
    gate_status: str
    counts: dict[str, int]
    created_at: datetime


class VariantView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    variant_id: str
    scenario_id: str
    category: Category
    gated: bool
    needs_rerun: bool
    baseline_result_id: int | None
    candidate_result_id: int | None
    baseline_verdict: str | None
    candidate_verdict: str | None
    baseline_scenario_hash: str | None
    candidate_scenario_hash: str | None
    new_critical_failures: list[str]
    resolved_critical_failures: list[str]
    new_failed_checks: list[str]
    fixed_checks: list[str]
    latency_delta_ms: float | None
    cost_delta_usd: Decimal | None
    explanation: str
    rerun_evidence: dict[str, Any] | None


class GateView(GateResult):
    thresholds: GateThresholds
    text: str  # the human-readable report


class ReportDetail(ReportSummary):
    thresholds: GateThresholds
    gate: GateResult
    metric_deltas: list[MetricDelta]
    baseline_metrics: dict[str, Any]
    candidate_metrics: dict[str, Any]
    variants: list[VariantView]
    revisions: list[int]  # report ids, oldest first; the first is the original comparison


class RerunRequest(BaseModel):
    repetitions: int = Field(default=3, ge=1, le=MAX_REPETITIONS)
    sides: Sides = Field(
        default="both",
        description="'both' also reruns the baseline, to tell a flaky variant from a regression.",
    )
    variant_ids: list[str] | None = Field(
        default=None, description="Defaults to the variants that need a rerun."
    )


class RerunView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    report_id: int
    repetitions: int
    sides: str
    variant_ids: list[str]
    status: str
    error: str | None
    result_report_id: int | None
    created_at: datetime
    finished_at: datetime | None
    test_run_ids: list[int] = []
    estimated_cost_usd: Decimal | None = None


def _report_or_404(db: Session, report_id: int) -> RegressionReportRecord:
    report = db.get(RegressionReportRecord, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Comparison not found.")
    return report


def _detail(db: Session, report: RegressionReportRecord) -> ReportDetail:
    return ReportDetail.model_validate(
        {
            **ReportSummary.model_validate(report).model_dump(),
            "thresholds": report.thresholds,
            "gate": report.gate,
            "metric_deltas": report.metric_deltas,
            "baseline_metrics": report.baseline_metrics,
            "candidate_metrics": report.candidate_metrics,
            "variants": report.variants,
            "revisions": [r.id for r in revisions(db, report)],
        }
    )


def _raise(exc: ComparisonError) -> HTTPException:
    return HTTPException(_ERROR_STATUS[exc.kind], str(exc))


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ReportDetail)
def create(request: CreateComparisonRequest, db: DbSession) -> ReportDetail:
    """Compare a candidate run with a baseline run and store the report. Deterministic."""
    try:
        report = create_comparison(
            db, request.baseline_run_id, request.candidate_run_id, request.thresholds
        )
    except ComparisonError as exc:
        raise _raise(exc) from exc
    return _detail(db, report)


@router.get("", response_model=list[ReportSummary])
def list_reports(
    db: DbSession,
    baseline_run_id: int | None = None,
    candidate_run_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[RegressionReportRecord]:
    query = select(RegressionReportRecord)
    if baseline_run_id is not None:
        query = query.where(RegressionReportRecord.baseline_run_id == baseline_run_id)
    if candidate_run_id is not None:
        query = query.where(RegressionReportRecord.candidate_run_id == candidate_run_id)
    return list(db.scalars(query.order_by(RegressionReportRecord.id.desc()).limit(limit)))


@router.get("/{report_id}", response_model=ReportDetail)
def get_report(report_id: int, db: DbSession) -> ReportDetail:
    return _detail(db, _report_or_404(db, report_id))


@router.get("/{report_id}/gate", response_model=GateView)
def get_gate(report_id: int, db: DbSession) -> GateView:
    report = _report_or_404(db, report_id)
    return GateView.model_validate(
        {**report.gate, "thresholds": report.thresholds, "text": render_report(report)}
    )


@router.get("/{report_id}/variants", response_model=list[VariantView])
def list_variants(
    report_id: int, db: DbSession, category: Category | None = None
) -> list[RegressionVariantRecord]:
    report = _report_or_404(db, report_id)
    return [v for v in report.variants if category is None or v.category == category]


def _rerun_view(
    db: Session, rerun: RegressionRerunRecord, cost: Decimal | None = None
) -> RerunView:
    view = RerunView.model_validate(rerun)
    view.test_run_ids = [r.id for r in rerun_runs(db, rerun.id)]
    view.estimated_cost_usd = cost
    return view


@router.post("/{report_id}/reruns", status_code=status.HTTP_202_ACCEPTED, response_model=RerunView)
def create_rerun(
    report_id: int,
    request: RerunRequest,
    background: BackgroundTasks,
    db: DbSession,
    llm: Annotated[LLMClient, Depends(get_llm_client)],
) -> RerunView:
    """Repeat variants k times (this calls Claude), then write a new report revision.

    The original report is left as it is.
    """
    report = _report_or_404(db, report_id)
    try:
        cost = estimate_rerun_cost(
            db, report, repetitions=request.repetitions, sides=request.sides,
            variant_ids=request.variant_ids,
        )  # fmt: skip
        rerun = request_rerun(
            db, report, repetitions=request.repetitions, sides=request.sides,
            variant_ids=request.variant_ids,
        )  # fmt: skip
    except ComparisonError as exc:
        raise _raise(exc) from exc
    except SelectionError as exc:  # a variant no longer exists in the scenario files
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    background.add_task(execute_rerun, rerun.id, session_factory=get_sessionmaker(), llm=llm)
    return _rerun_view(db, rerun, cost)


@router.get("/{report_id}/reruns", response_model=list[RerunView])
def list_reruns(report_id: int, db: DbSession) -> list[RerunView]:
    report = _report_or_404(db, report_id)
    root_id = report.root_report_id or report.id
    reruns = db.scalars(
        select(RegressionRerunRecord)
        .where(RegressionRerunRecord.report_id == root_id)
        .order_by(RegressionRerunRecord.id)
    )
    return [_rerun_view(db, r) for r in reruns]
