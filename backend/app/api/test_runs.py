from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.llm import LLMClient
from app.agent.versions import (
    DEFAULT_AGENT_VERSION,
    AgentVersionConflict,
    UnknownAgentVersion,
    get_version,
)
from app.api.agent import get_llm_client
from app.config import get_settings
from app.db.lab_models import (
    EvaluationCheckRecord,
    EvaluationResultRecord,
    TestRunRecord,
)
from app.db.session import DbSession, get_sessionmaker
from app.evaluation.judge import ClaudeJudge, Judge
from app.evaluation.metrics import RunMetrics, compute_metrics
from app.evaluation.service import (
    RunSelection,
    SelectionError,
    create_test_run,
    execute_test_run,
)
from app.evaluation.store import load_summaries

router = APIRouter(prefix="/test-runs", tags=["test runs"])


class CreateTestRunRequest(RunSelection):
    judge: bool = False
    agent_version: str = DEFAULT_AGENT_VERSION


class TestRunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    __test__ = False

    id: int
    status: str
    agent_version: str
    judge_model: str | None
    check_set_version: str | None  # null for runs graded before check sets were versioned
    variant_count: int
    error: str | None
    rerun_id: int | None  # set for runs that repeat variants for a regression rerun
    rerun_side: str | None
    repetition: int | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class TestRunDetail(TestRunSummary):
    agent_config: dict[str, Any]
    selection: dict[str, Any]
    results_recorded: int
    metrics: RunMetrics


class ResultSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    variant_id: str
    scenario_id: str
    persona: str
    verdict: str
    check_set_version: str | None
    critical_failures: int
    major_failures: int
    minor_failures: int
    final_status: str | None
    escalation_reason: str | None
    intent: str | None
    judge_verdict: str | None
    latency_ms: float
    llm_calls: int
    tool_calls: int
    estimated_cost_usd: Decimal | None


class CheckView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    target: str | None
    source: str
    severity: str
    passed: bool
    reason: str
    expected: Any
    actual: Any


class TraceView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    execution_id: str
    turns: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    initial_state: dict[str, Any] | None
    final_state: dict[str, Any] | None
    injected_faults: list[dict[str, Any]]


class ResultDetail(ResultSummary):
    scenario_hash: str
    final_response: str | None
    error: str | None
    models: list[str]
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    tool_errors: int
    judge_model: str | None
    judge_error: str | None
    judge_cost_usd: Decimal | None
    started_at: datetime
    checks: list[CheckView]
    trace: TraceView | None


class FailedCheck(CheckView):
    variant_id: str
    scenario_id: str


def get_judge_factory() -> Callable[[], Judge]:
    def make() -> Judge:
        api_key = get_settings().anthropic_api_key
        assert api_key is not None  # the LLM dependency already required it
        return ClaudeJudge(api_key.get_secret_value())

    return make


def _run_or_404(db: Session, test_run_id: int) -> TestRunRecord:
    run = db.get(TestRunRecord, test_run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Test run not found.")
    return run


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=TestRunSummary)
def create_run(
    request: CreateTestRunRequest,
    background: BackgroundTasks,
    db: DbSession,
    llm: Annotated[LLMClient, Depends(get_llm_client)],
    judge_factory: Annotated[Callable[[], Judge], Depends(get_judge_factory)],
) -> TestRunRecord:
    """Start a test run. It executes in the background; poll GET /test-runs/{id}."""
    try:
        get_version(request.agent_version)
    except UnknownAgentVersion as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    judge = judge_factory() if request.judge else None
    selection = RunSelection.model_validate(
        request.model_dump(include=set(RunSelection.model_fields))
    )
    try:
        run = create_test_run(
            db,
            selection,
            judge_model=judge.model if judge else None,
            agent_version=request.agent_version,
        )
    except SelectionError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except AgentVersionConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    background.add_task(
        execute_test_run, run.id, session_factory=get_sessionmaker(), llm=llm, judge=judge
    )
    return run


class TestRunListItem(TestRunSummary):
    # The snapshot stored when the run finished; null while it's queued or running.
    metrics: RunMetrics | None


def _stored_metrics(run: TestRunRecord) -> RunMetrics | None:
    if run.metrics is None:
        return None
    try:
        return RunMetrics.model_validate(run.metrics)
    except ValueError:  # a snapshot from an older metrics shape
        return None


@router.get("", response_model=list[TestRunListItem])
def list_runs(
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    agent_version: str | None = None,
    run_status: Annotated[
        str | None, Query(alias="status", pattern="^(queued|running|completed|failed)$")
    ] = None,
    include_reruns: bool = True,
) -> list[TestRunListItem]:
    query = select(TestRunRecord)
    if agent_version is not None:
        query = query.where(TestRunRecord.agent_version == agent_version)
    if run_status is not None:
        query = query.where(TestRunRecord.status == run_status)
    if not include_reruns:
        query = query.where(TestRunRecord.rerun_id.is_(None))
    runs = db.scalars(query.order_by(TestRunRecord.id.desc()).limit(limit))
    return [
        TestRunListItem.model_validate(
            {**TestRunSummary.model_validate(run).model_dump(), "metrics": _stored_metrics(run)}
        )
        for run in runs
    ]


@router.get("/{test_run_id}", response_model=TestRunDetail)
def get_run(test_run_id: int, db: DbSession) -> TestRunDetail:
    run = _run_or_404(db, test_run_id)
    summaries = load_summaries(db, test_run_id)
    return TestRunDetail.model_validate(
        {
            **TestRunSummary.model_validate(run).model_dump(),
            "agent_config": run.agent_config,
            "selection": run.selection,
            "results_recorded": len(summaries),
            "metrics": compute_metrics(summaries),
        }
    )


@router.get("/{test_run_id}/metrics", response_model=RunMetrics)
def get_metrics(test_run_id: int, db: DbSession) -> RunMetrics:
    _run_or_404(db, test_run_id)
    return compute_metrics(load_summaries(db, test_run_id))


@router.get("/{test_run_id}/results", response_model=list[ResultSummary])
def list_results(
    test_run_id: int,
    db: DbSession,
    verdict: Annotated[str | None, Query(pattern="^(pass|fail|error)$")] = None,
) -> list[EvaluationResultRecord]:
    _run_or_404(db, test_run_id)
    query = select(EvaluationResultRecord).where(EvaluationResultRecord.test_run_id == test_run_id)
    if verdict:
        query = query.where(EvaluationResultRecord.verdict == verdict)
    return list(db.scalars(query.order_by(EvaluationResultRecord.id)))


@router.get("/{test_run_id}/results/{variant_id}", response_model=ResultDetail)
def get_result(test_run_id: int, variant_id: str, db: DbSession) -> EvaluationResultRecord:
    result = db.scalars(
        select(EvaluationResultRecord).where(
            EvaluationResultRecord.test_run_id == test_run_id,
            EvaluationResultRecord.variant_id == variant_id,
        )
    ).one_or_none()
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Result not found.")
    return result


@router.get("/{test_run_id}/failed-checks", response_model=list[FailedCheck])
def list_failed_checks(
    test_run_id: int,
    db: DbSession,
    severity: Annotated[str | None, Query(pattern="^(critical|major|minor)$")] = None,
) -> list[FailedCheck]:
    _run_or_404(db, test_run_id)
    query = (
        select(
            EvaluationCheckRecord,
            EvaluationResultRecord.variant_id,
            EvaluationResultRecord.scenario_id,
        )
        .join(EvaluationResultRecord)
        .where(
            EvaluationResultRecord.test_run_id == test_run_id,
            EvaluationCheckRecord.passed.is_(False),
        )
        .order_by(EvaluationCheckRecord.id)
    )
    if severity:
        query = query.where(EvaluationCheckRecord.severity == severity)
    return [
        FailedCheck.model_validate(
            {**CheckView.model_validate(check).model_dump(), "variant_id": vid, "scenario_id": sid}
        )
        for check, vid, sid in db.execute(query)
    ]
