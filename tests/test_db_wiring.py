"""DB wiring that must hold without a reachable database."""

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_session
from app.main import create_app
from tests.conftest import make_settings


def test_readyz_503_and_healthz_200_when_the_database_is_down():
    # Port 1 refuses immediately, like a DB host that is down.
    app = create_app(make_settings(database_url="postgresql://u:p@127.0.0.1:1/skku"))
    with TestClient(app) as client:
        res = client.get("/api/readyz")
        assert res.status_code == 503
        assert res.json() == {"ok": False, "failed": ["database"]}
        assert client.get("/api/healthz").status_code == 200


def test_no_database_url_means_no_engine_and_a_clear_503():
    app = create_app(make_settings())
    assert app.state.engine is None

    @app.get("/probe")
    def probe(session: Session = Depends(get_session)):
        return {}

    res = TestClient(app).get("/probe")
    assert res.status_code == 503
    assert "데이터베이스" in res.json()["detail"]


@pytest.mark.parametrize(
    "given",
    [
        "postgres://u:p@h:5432/d",
        "postgresql://u:p@h:5432/d",
        "postgresql+psycopg://u:p@h:5432/d",
    ],
)
def test_sqlalchemy_url_uses_the_psycopg_driver(given):
    s = Settings(_env_file=None, database_url=given)
    assert s.sqlalchemy_url == "postgresql+psycopg://u:p@h:5432/d"


def test_sqlalchemy_url_requires_database_url():
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        Settings(_env_file=None).sqlalchemy_url  # noqa: B018
