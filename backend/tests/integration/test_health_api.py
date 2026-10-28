import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_readiness_ok_against_real_database(client: TestClient) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
