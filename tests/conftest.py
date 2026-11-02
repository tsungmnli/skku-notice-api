import pytest
from fastapi.testclient import TestClient
from pathlib import Path

from app.config import Settings
from app.main import create_app


_FIXTURES = Path(__file__).parent / "crawler" / "fixtures"

collect_ignore = (
    []
    if _FIXTURES.is_dir()
    else [
        "crawler/test_audit_deadlines.py",
        "crawler/test_parser.py",
        "crawler/test_detail.py",
        "crawler/test_run.py",
        "db/test_crawler_store.py",
        "db/test_crawler_details.py",
    ]
)


def make_settings(**overrides) -> Settings:
    # _env_file=None keeps a developer's local .env out of the tests.
    values = {"app_env": "test", "cors_origins": ["http://localhost:3000"], **overrides}
    return Settings(_env_file=None, **values) # type: ignore


@pytest.fixture
def app():
    return create_app(make_settings())


@pytest.fixture
def client(app):
    return TestClient(app)
