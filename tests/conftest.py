import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_settings(**overrides) -> Settings:
    # _env_file=None keeps a developer's local .env out of the tests.
    values = {"app_env": "test", "cors_origins": ["http://localhost:3000"], **overrides}
    return Settings(_env_file=None, **values)


@pytest.fixture
def app():
    return create_app(make_settings())


@pytest.fixture
def client(app):
    return TestClient(app)
