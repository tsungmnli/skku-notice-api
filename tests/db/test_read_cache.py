"""Cache-Control on the public read endpoints (app/cache.py)."""

import pytest

PUBLIC = "public, max-age=60"


@pytest.mark.parametrize(
    "path",
    ["/api/v1/articles", "/api/v1/articles/1", "/api/v1/categories", "/api/v1/writers"],
)
def test_public_reads_can_be_cached_for_a_minute(api, path):
    r = api.get(path)
    assert r.status_code == 200
    assert r.headers["cache-control"] == PUBLIC


@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("/api/v1/articles/999999", 404),  # a notice that is not there *yet*
        ("/api/v1/articles?page=0", 422),
        ("/api/v1/articles?sort=nope", 422),
    ],
)
def test_errors_are_never_cached(api, path, status):
    r = api.get(path)
    assert r.status_code == status
    assert "cache-control" not in r.headers


def test_lookup_is_a_post_and_is_not_cached(api):
    r = api.post("/api/v1/articles/lookup", json={"ids": [1]})
    assert r.status_code == 200
    assert "cache-control" not in r.headers


def test_the_calendar_keeps_its_own_longer_cache(api):
    assert api.get("/api/v1/calendar?id=1").headers["cache-control"] == "public, max-age=300"
