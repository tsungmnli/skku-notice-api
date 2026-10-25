from fastapi import FastAPI, Query
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import make_settings


def test_validation_error_is_a_single_detail_string():
    app = create_app(make_settings())

    @app.get("/probe")
    def probe(size: int = Query(...)):
        return size

    res = TestClient(app).get("/probe?size=abc")
    assert res.status_code == 422
    body = res.json()
    assert list(body) == ["detail"]
    assert isinstance(body["detail"], str)
    assert "size" in body["detail"]


def test_unknown_route_is_json_detail(client):
    res = client.get("/api/v1/nope")
    assert res.status_code == 404
    assert isinstance(res.json()["detail"], str)


def test_cors_allows_configured_origin_only(client):
    ok = client.get("/api/healthz", headers={"Origin": "http://localhost:3000"})
    assert ok.headers["access-control-allow-origin"] == "http://localhost:3000"

    other = client.get("/api/healthz", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in other.headers


def test_docs_served_under_prefix_in_dev(client):
    assert client.get("/api/docs").status_code == 200
    assert client.get("/api/openapi.json").status_code == 200


def test_docs_hidden_when_disabled():
    app: FastAPI = create_app(make_settings(docs_enabled=False))
    c = TestClient(app)
    assert c.get("/api/docs").status_code == 404
    assert c.get("/api/openapi.json").status_code == 404
