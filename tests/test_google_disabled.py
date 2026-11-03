"""Google sign-in not configured: the browser is sent back to /login, not shown a JSON 404."""

from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import make_settings


def test_google_start_without_config_redirects_to_login():
    client = TestClient(create_app(make_settings(public_url="https://example.test")))
    r = client.get("/api/v1/auth/google?next=/favorites", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["cache-control"] == "no-store"
    parts = urlsplit(r.headers["location"])
    assert (parts.scheme, parts.netloc, parts.path) == ("https", "example.test", "/login")
    assert parse_qs(parts.query) == {"error": ["google_disabled"]}
