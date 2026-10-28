"""Reliability Lab tables (Part 2). The store tables (Part 1) live in models.py."""

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func, true
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
