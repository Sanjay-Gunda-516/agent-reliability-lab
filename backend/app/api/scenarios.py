"""Read-only view of the scenario catalogue, as last synced from backend/scenarios/*.yaml."""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.db.lab_models import ScenarioRecord
from app.db.session import DbSession

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


class VariantView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    persona: str
    turns: list[str]
    is_active: bool


class ScenarioSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    tags: list[str]
    customer_id: int
    expected_outcome: str
    expected_status: str
    content_hash: str
    is_active: bool
    updated_at: datetime
    variants: list[VariantView]


class ScenarioDetail(ScenarioSummary):
    # The validated definition: expected behaviour, tool expectations, state, faults.
    definition: dict[str, Any]


@router.get("", response_model=list[ScenarioSummary])
def list_scenarios(
    db: DbSession,
    tag: str | None = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> list[ScenarioRecord]:
    query = select(ScenarioRecord).order_by(ScenarioRecord.id)
    if not include_inactive:
        query = query.where(ScenarioRecord.is_active)
    if tag:
        query = query.where(ScenarioRecord.tags.contains([tag]))
    return list(db.scalars(query))


@router.get("/{scenario_id}", response_model=ScenarioDetail)
def get_scenario(scenario_id: str, db: DbSession) -> ScenarioRecord:
    scenario = db.get(ScenarioRecord, scenario_id)
    if scenario is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scenario not found.")
    return scenario
