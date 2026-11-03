"""Body limit, no-store and Origin check, through a real app (no database needed)."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import make_settings

WEB = "http://127.0.0.1:3000"


@pytest.fixture
def app():
    app = create_app(make_settings())

    @app.post("/api/v1/auth/echo")
    def echo_auth(body: dict):
        return body

    @app.post("/api/probe")
    def probe(body: dict):
        return {"ok": True}

    @app.delete("/api/probe")
    def probe_delete():
        return {"ok": True}

    @app.get("/api/v1/me/probe")
    def me_probe():
        return {"ok": True}

    return app


@pytest.fixture
def c(app):
    return TestClient(app)


# --- body limit ---------------------------------------------------------------------------


def test_small_bodies_pass(c):
    assert c.post("/api/probe", json={"a": "x" * 1000}, headers={"Origin": WEB}).status_code == 200


def test_declared_oversize_body_is_413(c):
    r = c.post("/api/probe", json={"a": "x" * 70_000}, headers={"Origin": WEB})
    assert r.status_code == 413
    assert r.json()["code"] == "payload_too_large"
    assert r.headers["access-control-allow-origin"] == WEB  # CORS still applied


def test_streamed_oversize_body_is_413(c):
    def chunks():
        for _ in range(80):
            yield b'{"a": "' + b"x" * 1000 if _ == 0 else b"x" * 1000
        yield b'"}'

    r = c.post("/api/probe", content=chunks(), headers={"Origin": WEB})
    assert r.status_code == 413


# --- no-store -----------------------------------------------------------------------------


def test_account_paths_are_no_store_and_others_are_not(c):
    assert c.get("/api/v1/me/probe").headers["cache-control"] == "no-store"
    assert (
        c.post("/api/v1/auth/echo", json={}, headers={"Origin": WEB}).headers["cache-control"]
        == "no-store"
    )
    assert "cache-control" not in c.get("/api/healthz").headers


def test_existing_cache_control_is_kept(app):
    from fastapi.responses import JSONResponse

    @app.get("/api/v1/auth/cached")
    def cached():
        return JSONResponse({}, headers={"Cache-Control": "private, max-age=5"})

    assert (
        TestClient(app).get("/api/v1/auth/cached").headers["cache-control"] == "private, max-age=5"
    )


def test_calendar_feed_keeps_its_own_caching(client):
    r = client.get("/api/v1/calendar")  # 422/503 without ids or db, but never no-store
    assert r.headers.get("cache-control") != "no-store"


# --- origin check -------------------------------------------------------------------------


def test_foreign_origin_is_refused_even_without_a_cookie(c):
    r = c.post("/api/probe", json={}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["code"] == "origin_not_allowed"


def test_configured_origin_is_accepted(c):
    assert c.post("/api/probe", json={}, headers={"Origin": WEB}).status_code == 200


def test_same_host_origin_is_accepted(c):
    r = c.post("/api/probe", json={}, headers={"Origin": "http://testserver"})
    assert r.status_code == 200


def test_null_origin_is_refused(c):
    assert c.post("/api/probe", json={}, headers={"Origin": "null"}).status_code == 403


def test_cookie_without_origin_is_refused_but_bearer_is_not(c):
    c.cookies.set("sid", "tok")
    assert c.post("/api/probe", json={}).status_code == 403
    assert c.delete("/api/probe").status_code == 403
    ok = c.post("/api/probe", json={}, headers={"Authorization": "Bearer tok"})
    assert ok.status_code == 200


def test_no_cookie_no_origin_is_allowed_for_native_clients(c):
    assert c.post("/api/probe", json={}).status_code == 200


def test_safe_methods_are_never_checked(c):
    c.cookies.set("sid", "tok")
    assert c.get("/api/healthz", headers={"Origin": "https://evil.example"}).status_code == 200


def test_public_url_origin_counts_as_ours():
    app = create_app(make_settings(public_url="https://skku.example.com", cors_origins=[]))

    @app.post("/api/probe")
    def probe():
        return {"ok": True}

    r = TestClient(app).post("/api/probe", headers={"Origin": "https://skku.example.com"})
    assert r.status_code == 200


# --- CORS ---------------------------------------------------------------------------------


def test_preflight_allows_credentials_and_the_new_methods_and_headers(c):
    r = c.options(
        "/api/v1/me",
        headers={
            "Origin": WEB,
            "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": "authorization, x-client, content-type",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-credentials"] == "true"
    assert "DELETE" in r.headers["access-control-allow-methods"]
    allowed = r.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed and "x-client" in allowed
