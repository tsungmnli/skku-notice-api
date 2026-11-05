from pathlib import Path

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from app.auth import passwords
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
    values = {"app_env": "test", "cors_origins": ["http://127.0.0.1:3000"], **overrides}
    return Settings(_env_file=None, **values)  # type: ignore


@pytest.fixture
def app():
    return create_app(make_settings())


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture(autouse=True)
def fast_hasher(monkeypatch):
    """Cheap Argon2 parameters for every test: signing up or logging in with the production ones
    (64 MiB, t=3) costs ~0.1 s a time, and the account tests do it hundreds of times. The
    production parameters themselves are checked in tests/auth/test_passwords.py."""
    monkeypatch.setattr(
        passwords, "_hasher", PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    )
    passwords._dummy_hash.cache_clear()
    yield
    passwords._dummy_hash.cache_clear()
