from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import make_settings


def test_readyz_is_ok_when_the_database_answers(migrated_db_url):
    app = create_app(make_settings(database_url=migrated_db_url))
    with TestClient(app) as client:
        assert client.get("/api/readyz").json() == {"ok": True}
