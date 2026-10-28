from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--live", action="store_true", help="run tests that call the real Claude API")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # An explicit flag rather than `-m live`: a different -m filter must never be able to
    # pull paid API calls into an ordinary run.
    if config.getoption("--live"):
        return
    skip_live = pytest.mark.skip(reason="calls the real Claude API; pass --live to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
