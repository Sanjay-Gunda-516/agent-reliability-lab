import logging
from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import DbSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["health"])


class ReadinessResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    database: Literal["ok", "unavailable"]


@router.get("")
def liveness() -> dict[str, str]:
    # Kept dependency-free so a database outage doesn't make the orchestrator
    # restart otherwise healthy containers.
    return {"status": "ok"}


@router.get(
    "/ready",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
def readiness(db: DbSession, response: Response) -> ReadinessResponse:
    try:
        db.execute(text("select 1"))
    except SQLAlchemyError:
        logger.warning("Readiness check failed: database unreachable", exc_info=True)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(status="unavailable", database="unavailable")
    return ReadinessResponse(status="ok", database="ok")
