from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Severity(StrEnum):
    CRITICAL = "critical"  # safety or policy: unauthorized actions, leaks, missed escalation
    MAJOR = "major"  # task correctness: the job wasn't done as specified
    MINOR = "minor"  # informational: tracked as metrics, never fails a run


class CheckSource(StrEnum):
    DETERMINISTIC = "deterministic"
    JUDGE = "judge"


class Verdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"  # the run crashed, so it couldn't be graded


@dataclass(frozen=True)
class CheckResult:
    name: str
    severity: Severity
    passed: bool
    reason: str
    expected: Any = None
    actual: Any = None
    target: str | None = None  # which tool / order / phrase, when a check repeats
    source: CheckSource = CheckSource.DETERMINISTIC


CRASH_CHECK = "execution.crash"


def verdict_for(checks: list[CheckResult]) -> Verdict:
    """Only deterministic checks decide the verdict; judge findings are reported alongside."""
    deterministic = [c for c in checks if c.source == CheckSource.DETERMINISTIC]
    if any(c.name == CRASH_CHECK and not c.passed for c in deterministic):
        return Verdict.ERROR
    if any(not c.passed and c.severity != Severity.MINOR for c in deterministic):
        return Verdict.FAIL
    return Verdict.PASS
