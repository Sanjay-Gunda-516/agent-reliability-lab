"""Guard: grading code can't change without a decision about the check-set version."""

import hashlib
from pathlib import Path

import app.evaluation
from app.evaluation.checks import CHECK_SET_VERSION

GRADING_FILES = ("checks.py", "results.py")

# Update both lines together when grading code changes. Bump CHECK_SET_VERSION in
# checks.py if any check result or verdict could differ; if the change can't affect
# grading (a comment, a rename), keep the version and just update the fingerprint.
EXPECTED_VERSION = "checks-v2"
EXPECTED_FINGERPRINT = "0a9666dbcfa2e2476a58a875b2a8045ff81a8f74ec733a0b9586363a344b3909"


def fingerprint() -> str:
    package = Path(app.evaluation.__file__).parent
    digest = hashlib.sha256()
    for name in GRADING_FILES:
        digest.update((package / name).read_bytes())
    return digest.hexdigest()


def test_grading_code_matches_its_recorded_version() -> None:
    assert (CHECK_SET_VERSION, fingerprint()) == (EXPECTED_VERSION, EXPECTED_FINGERPRINT), (
        "app/evaluation/checks.py or results.py changed. Decide whether CHECK_SET_VERSION "
        "needs a bump, then update EXPECTED_VERSION and EXPECTED_FINGERPRINT here."
    )
