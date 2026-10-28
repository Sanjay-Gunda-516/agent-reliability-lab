from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.lab_models import ScenarioRecord, ScenarioVariantRecord
from app.scenarios.schema import Scenario, variant_id


@dataclass
class SyncReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    deactivated: list[str] = field(default_factory=list)


def _apply(record: ScenarioRecord, scenario: Scenario) -> None:
    record.title = scenario.title
    record.tags = [str(tag) for tag in scenario.tags]
    record.customer_id = scenario.customer_id
    record.expected_outcome = scenario.expected.outcome
    record.expected_status = scenario.expected.final_status
    record.definition = scenario.model_dump(mode="json")
    record.content_hash = scenario.content_hash()
    record.is_active = True

    existing = {v.id: v for v in record.variants}
    for persona, turns in scenario.variants.items():
        vid = variant_id(scenario.id, persona)
        variant = existing.pop(vid, None)
        if variant is None:
            variant = ScenarioVariantRecord(id=vid, persona=str(persona))
            record.variants.append(variant)
        variant.turns = list(turns)
        variant.is_active = True
    # Results will reference variants, so removed ones are deactivated, not deleted.
    for variant in existing.values():
        variant.is_active = False


def sync_scenarios(db: Session, scenarios: list[Scenario]) -> SyncReport:
    report = SyncReport()
    records = {r.id: r for r in db.scalars(select(ScenarioRecord))}

    for scenario in scenarios:
        record = records.pop(scenario.id, None)
        if record is None:
            record = ScenarioRecord(id=scenario.id, variants=[])
            db.add(record)
            _apply(record, scenario)
            report.created.append(scenario.id)
        elif record.content_hash != scenario.content_hash() or not record.is_active:
            _apply(record, scenario)
            report.updated.append(scenario.id)
        else:
            report.unchanged.append(scenario.id)

    for record in records.values():
        if record.is_active:
            record.is_active = False
            for variant in record.variants:
                variant.is_active = False
            report.deactivated.append(record.id)

    db.commit()
    return report
