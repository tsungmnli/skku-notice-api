from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.main import create_app
from tests.conftest import make_settings


def test_readyz_is_ok_when_the_database_answers(migrated_db_url):
    app = create_app(make_settings(database_url=migrated_db_url))
    with TestClient(app) as client:
        assert client.get("/api/readyz").json() == {"ok": True}


def test_get_session_gives_a_working_session(migrated_db_url):
    app = create_app(make_settings(database_url=migrated_db_url))

    @app.get("/probe")
    def probe(session: Session = Depends(get_session)):
        return {"articles": session.scalar(text("SELECT count(*) FROM articles"))}

    with TestClient(app) as client:
        assert client.get("/probe").json() == {"articles": 0}
