from pathlib import Path

import yaml
from pydantic import ValidationError

from app.config import BACKEND_DIR
from app.scenarios.schema import Scenario

SCENARIO_DIR = BACKEND_DIR / "scenarios"


class ScenarioLoadError(Exception):
    pass


def load_scenario_file(path: Path) -> list[Scenario]:
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, list):
        raise ScenarioLoadError(f"{path.name}: expected a list of scenarios")

    scenarios = []
    for index, item in enumerate(raw):
        label = item.get("id", f"#{index}") if isinstance(item, dict) else f"#{index}"
        try:
            scenarios.append(Scenario.model_validate(item))
        except ValidationError as exc:
            raise ScenarioLoadError(f"{path.name} {label}: {exc}") from exc
    return scenarios


def load_scenarios(directory: Path = SCENARIO_DIR) -> list[Scenario]:
    scenarios: list[Scenario] = []
    seen: dict[str, str] = {}
    for path in sorted(directory.glob("*.yaml")):
        for scenario in load_scenario_file(path):
            if scenario.id in seen:
                raise ScenarioLoadError(
                    f"duplicate scenario id {scenario.id!r} in {path.name} and {seen[scenario.id]}"
                )
            seen[scenario.id] = path.name
            scenarios.append(scenario)
    return scenarios
