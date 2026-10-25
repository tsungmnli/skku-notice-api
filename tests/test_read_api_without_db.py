"""The v1 endpoints answer 503 (not 500) when no database is configured."""

import pytest


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("get", "/api/v1/articles", {}),
        ("get", "/api/v1/articles/1", {}),
        ("post", "/api/v1/articles/lookup", {"json": {"ids": [1]}}),
        ("get", "/api/v1/categories", {}),
        ("get", "/api/v1/writers", {}),
        ("get", "/api/v1/calendar?id=1", {}),
    ],
)
def test_503_without_a_database(client, method, path, kwargs):
    res = getattr(client, method)(path, **kwargs)
    assert res.status_code == 503
    assert "데이터베이스" in res.json()["detail"]
