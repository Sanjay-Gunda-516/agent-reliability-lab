"""Human approval queue: list pending decisions and approve or reject them.

Deciding resumes the paused agent execution in this request: the approved action runs
(at most once) or is replaced by the rejection, and Claude writes the follow-up for the
customer. The reviewer is passed in the request body; there's no staff login yet.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.agent import approvals
from app.agent.config import AgentConfig
from app.agent.llm import LLMClient
from app.agent.service import NotPaused, resume_agent
from app.api.agent import get_llm_client
from app.db.lab_models import ExecutionTraceRecord, HumanApprovalRecord
from app.db.session import DbSession, get_engine
from app.observability.context import correlate
from app.observability.trace_store import append_to_trace, continue_recorder

router = APIRouter(prefix="/approvals", tags=["approvals"])


class ApprovalView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    execution_id: str
    tool_name: str
    arguments: dict[str, Any]
    customer_id: int
    order_id: int | None
    refund_amount: Decimal | None
    risk_level: str
    reason: str
    customer_reason: str | None
    status: str
    decided_by: str | None
    decision_note: str | None
    decided_at: datetime | None
    action_status: str
    action_result: Any
    resumed_at: datetime | None
    created_at: datetime


class DecisionRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=100, description="Who made the decision.")
    note: str | None = Field(default=None, max_length=1000)


class DecisionResponse(BaseModel):
    approval: ApprovalView
    execution_id: str
    execution_status: str
    final_response: str


@contextmanager
def _decision_lock(approval_id: int) -> Iterator[bool]:
    """One decision per approval at a time, held for the whole resume.

    A session-level advisory lock on its own connection: the resume commits several
    times, which would release a row lock, and if this process dies the lock goes with it.
    """
    with get_engine().connect() as conn:
        acquired = bool(conn.scalar(select(func.pg_try_advisory_lock(approval_id))))
        try:
            yield acquired
        finally:
            if acquired:
                conn.scalar(select(func.pg_advisory_unlock(approval_id)))
            conn.commit()


def _approval_or_404(db: Session, approval_id: int) -> HumanApprovalRecord:
    approval = db.get(HumanApprovalRecord, approval_id)
    if approval is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval not found.")
    return approval


def _record_decision(
    db: Session, approval_id: int, decision: str, request: DecisionRequest
) -> HumanApprovalRecord:
    decided = db.execute(
        update(HumanApprovalRecord)
        .where(
            HumanApprovalRecord.id == approval_id, HumanApprovalRecord.status == approvals.PENDING
        )
        .values(
            status=decision,
            decided_by=request.reviewer,
            decision_note=request.note,
            decided_at=datetime.now(UTC),
        )
    )
    db.commit()
    approval = _approval_or_404(db, approval_id)
    db.refresh(approval)
    if decided.rowcount:  # type: ignore[attr-defined]
        return approval
    # Already decided. The only thing allowed is finishing a resume that crashed,
    # with the same decision.
    if approval.status == decision and approval.resumed_at is None:
        return approval
    raise HTTPException(
        status.HTTP_409_CONFLICT,
        f"Approval {approval_id} was already {approval.status} by {approval.decided_by}.",
    )


def _decide(
    approval_id: int,
    decision: Literal["approved", "rejected"],
    request: DecisionRequest,
    db: Session,
    llm: LLMClient,
) -> DecisionResponse:
    _approval_or_404(db, approval_id)
    with _decision_lock(approval_id) as acquired:
        if not acquired:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "A decision for this approval is already being processed."
            )
        approval = _record_decision(db, approval_id, decision, request)
        trace = db.scalars(
            select(ExecutionTraceRecord).where(
                ExecutionTraceRecord.execution_id == approval.execution_id
            )
        ).one()
        recorder = continue_recorder(trace)
        with correlate(execution_id=trace.execution_id):
            try:
                result = resume_agent(
                    db=db,
                    llm=llm,
                    customer_id=approval.customer_id,
                    recorder=recorder,
                    config=AgentConfig(),
                )
            except NotPaused as exc:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "This execution is no longer waiting for a decision."
                ) from exc
            append_to_trace(
                trace,
                events=recorder.events,
                tool_calls=result.tool_calls,
                messages=result.messages,
                status=result.status,
                escalation_reason=result.escalation_reason,
                finished_at=datetime.now(UTC),
                turn_update={
                    "status": result.status,
                    "final_response": result.final_response,
                    "approval": {"id": approval.id, "decision": decision},
                },
            )
            db.commit()
        db.refresh(approval)
        return DecisionResponse(
            approval=ApprovalView.model_validate(approval),
            execution_id=trace.execution_id,
            execution_status=result.status,
            final_response=result.final_response,
        )


@router.get("", response_model=list[ApprovalView])
def list_approvals(
    db: DbSession,
    approval_status: Annotated[
        str | None, Query(alias="status", pattern="^(pending|approved|rejected)$")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[HumanApprovalRecord]:
    query = select(HumanApprovalRecord)
    if approval_status:
        query = query.where(HumanApprovalRecord.status == approval_status)
    return list(db.scalars(query.order_by(HumanApprovalRecord.id.desc()).limit(limit)))


@router.get("/{approval_id}", response_model=ApprovalView)
def get_approval(approval_id: int, db: DbSession) -> HumanApprovalRecord:
    return _approval_or_404(db, approval_id)


@router.post("/{approval_id}/approve", response_model=DecisionResponse)
def approve(
    approval_id: int,
    request: DecisionRequest,
    db: DbSession,
    llm: Annotated[LLMClient, Depends(get_llm_client)],
) -> DecisionResponse:
    return _decide(approval_id, "approved", request, db, llm)


@router.post("/{approval_id}/reject", response_model=DecisionResponse)
def reject(
    approval_id: int,
    request: DecisionRequest,
    db: DbSession,
    llm: Annotated[LLMClient, Depends(get_llm_client)],
) -> DecisionResponse:
    return _decide(approval_id, "rejected", request, db, llm)
