"""Scenario commands. Run from backend/:

python -m app.scenarios check   validate the YAML files and summarise them
python -m app.scenarios sync    load them into the database
"""

import argparse
import sys
from collections import Counter

from app.db.session import get_sessionmaker
from app.scenarios.loader import ScenarioLoadError, load_scenarios
from app.scenarios.sync import sync_scenarios


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.scenarios")
    parser.add_argument("command", choices=["check", "sync"])
    args = parser.parse_args(argv)

    try:
        scenarios = load_scenarios()
    except ScenarioLoadError as exc:
        print(f"Invalid scenario: {exc}", file=sys.stderr)
        return 1

    variants = sum(len(s.variants) for s in scenarios)
    print(f"{len(scenarios)} scenarios, {variants} variants")
    if args.command == "check":
        for tag, count in sorted(Counter(t for s in scenarios for t in s.tags).items()):
            print(f"  {tag:<18} {count}")
        return 0

    with get_sessionmaker()() as db:
        report = sync_scenarios(db, scenarios)
    print(
        f"created {len(report.created)}, updated {len(report.updated)}, "
        f"unchanged {len(report.unchanged)}, deactivated {len(report.deactivated)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
