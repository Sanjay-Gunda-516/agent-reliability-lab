"""Agent versions: defined in code, recorded in the database, never silently redefined."""

import dataclasses
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from app.agent import versions
from app.agent.config import AgentConfig
from app.agent.versions import (
    AGENT_VERSIONS,
    AgentVersion,
    AgentVersionConflict,
    UnknownAgentVersion,
    config_hash,
    get_version,
    sync_agent_versions,
)
from app.db.lab_models import AgentVersionRecord

pytestmark = pytest.mark.integration


def test_sync_records_every_version_defined_in_code(db_session: Session) -> None:
    records = {r.name: r for r in sync_agent_versions(db_session)}

    assert set(AGENT_VERSIONS) <= set(records)
    v1 = records["v1"]
    assert v1.defined_in_code and v1.model == "claude-opus-5" and v1.prompt_version == "v1"
    assert v1.config == dataclasses.asdict(AgentConfig())
    assert v1.config_hash == AGENT_VERSIONS["v1"].config_hash()
    assert records["v1-regressed"].config_hash != v1.config_hash


def test_hash_covers_the_prompt_text_not_just_its_name(monkeypatch: pytest.MonkeyPatch) -> None:
    config = dataclasses.asdict(AgentConfig())
    before = config_hash(config)

    monkeypatch.setitem(versions.SYSTEM_PROMPTS, "v1", "A different prompt.")

    assert config_hash(config) != before


def test_changing_an_existing_version_is_refused(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_agent_versions(db_session)
    changed = AgentVersion("v1", "Same name, new effort.", AgentConfig(effort="high"))
    monkeypatch.setitem(versions.AGENT_VERSIONS, "v1", changed)

    with pytest.raises(AgentVersionConflict, match="new version name"):
        sync_agent_versions(db_session)


def test_versions_only_known_from_old_runs_are_kept(db_session: Session) -> None:
    db_session.add(
        AgentVersionRecord(
            name="default",
            description="Recorded from test runs made before agent versions existed.",
            config={"model": "claude-opus-5"},
            config_hash="x" * 64,
            model="claude-opus-5",
            prompt_version=None,
            defined_in_code=False,
            created_at=datetime.now(UTC),
        )
    )

    records = {r.name: r for r in sync_agent_versions(db_session)}

    assert records["default"].defined_in_code is False


def test_backfilled_record_with_the_same_config_is_adopted(db_session: Session) -> None:
    sync_agent_versions(db_session)
    record = db_session.get(AgentVersionRecord, "v1")
    assert record is not None
    record.config_hash, record.defined_in_code = "legacy" + "0" * 58, False

    sync_agent_versions(db_session)

    assert record.defined_in_code and record.config_hash == AGENT_VERSIONS["v1"].config_hash()


def test_unknown_version_names_the_known_ones() -> None:
    with pytest.raises(UnknownAgentVersion, match="v1-regressed"):
        get_version("v9")
