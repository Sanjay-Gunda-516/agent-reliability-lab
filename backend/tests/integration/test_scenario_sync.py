import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.lab_models import ScenarioRecord, ScenarioVariantRecord
from app.scenarios.__main__ import main
from app.scenarios.loader import load_scenarios
from app.scenarios.schema import Persona, Scenario
from app.scenarios.sync import sync_scenarios

pytestmark = pytest.mark.integration

CATALOGUE = load_scenarios()


def pick(scenario_id: str) -> Scenario:
    return next(s for s in CATALOGUE if s.id == scenario_id)


def record(db: Session, scenario_id: str) -> ScenarioRecord:
    db.expire_all()
    found = db.get(ScenarioRecord, scenario_id)
    assert found is not None
    return found


def test_first_sync_creates_scenarios_and_variants(db_session: Session) -> None:
    report = sync_scenarios(db_session, CATALOGUE)

    assert len(report.created) == len(CATALOGUE)
    stored = record(db_session, "cancel-eligible-order")
    assert stored.expected_outcome == "order_cancelled"
    assert "happy-path" in stored.tags
    assert stored.content_hash == pick("cancel-eligible-order").content_hash()
    assert Scenario.model_validate(stored.definition) == pick("cancel-eligible-order")
    assert {v.id for v in stored.variants} == {
        "cancel-eligible-order:normal",
        "cancel-eligible-order:angry",
        "cancel-eligible-order:confused",
    }


def test_resync_without_changes_is_a_no_op(db_session: Session) -> None:
    sync_scenarios(db_session, CATALOGUE)

    report = sync_scenarios(db_session, CATALOGUE)

    assert report.created == report.updated == report.deactivated == []
    assert len(report.unchanged) == len(CATALOGUE)


def test_edited_scenario_gets_a_new_hash(db_session: Session) -> None:
    original = pick("cancel-shipped-order")
    sync_scenarios(db_session, [original])
    edited = original.model_copy(update={"title": "Refuse cancelling shipped orders"})

    report = sync_scenarios(db_session, [edited])

    assert report.updated == ["cancel-shipped-order"]
    assert record(db_session, "cancel-shipped-order").content_hash == edited.content_hash()


def test_removed_scenarios_and_variants_are_deactivated_not_deleted(db_session: Session) -> None:
    keep, drop = pick("cancel-eligible-order"), pick("order-not-found")
    sync_scenarios(db_session, [keep, drop])
    fewer_variants = keep.model_copy(
        update={"variants": {Persona.NORMAL: keep.variants[Persona.NORMAL]}}
    )

    report = sync_scenarios(db_session, [fewer_variants])

    assert report.deactivated == ["order-not-found"]
    assert record(db_session, "order-not-found").is_active is False
    active = {
        v.id: v.is_active
        for v in db_session.scalars(
            select(ScenarioVariantRecord).where(
                ScenarioVariantRecord.scenario_id == "cancel-eligible-order"
            )
        )
    }
    assert active == {
        "cancel-eligible-order:normal": True,
        "cancel-eligible-order:angry": False,
        "cancel-eligible-order:confused": False,
    }


def test_re_added_scenario_is_reactivated(db_session: Session) -> None:
    scenario = pick("order-not-found")
    sync_scenarios(db_session, [scenario])
    sync_scenarios(db_session, [])

    report = sync_scenarios(db_session, [scenario])

    assert report.updated == ["order-not-found"]
    assert record(db_session, "order-not-found").is_active is True


def test_check_command_validates_the_catalogue(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check"]) == 0

    out = capsys.readouterr().out
    assert f"{len(CATALOGUE)} scenarios" in out
    assert "adversarial" in out
