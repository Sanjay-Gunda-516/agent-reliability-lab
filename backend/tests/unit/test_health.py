from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.db.session import get_db
from app.main import app


class FakeSession:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def execute(self, *args: Any, **kwargs: Any) -> None:
        if self.error:
            raise self.error


def test_liveness_does_not_touch_the_database(client: TestClient) -> None:
    app.dependency_overrides[get_db] = lambda: FakeSession(AssertionError("db was queried"))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_ok_when_database_responds(client: TestClient) -> None:
    app.dependency_overrides[get_db] = lambda: FakeSession()

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_readiness_503_when_database_unreachable(client: TestClient) -> None:
    error = OperationalError("select 1", {}, Exception("connection refused"))
    app.dependency_overrides[get_db] = lambda: FakeSession(error)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "database": "unavailable"}
