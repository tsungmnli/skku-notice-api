"""Fixtures for tests that need a real PostgreSQL (the schema uses Postgres-only features).

Each test gets its own throwaway database on the server named by TEST_DATABASE_URL.
"""

import os
import uuid
from datetime import datetime

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, make_url
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.migrate import run as migrate
from app.timeutil import KST, get_now
from scripts import seed_dev
from tests.conftest import make_settings

ADMIN_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://skku:dev@localhost:5432/postgres")


@pytest.fixture(scope="session")
def admin_conn():
    try:
        conn = psycopg.connect(ADMIN_URL, autocommit=True, connect_timeout=2)
    except psycopg.OperationalError as exc:
        msg = (
            f"PostgreSQL is not reachable via TEST_DATABASE_URL ({exc}). "
            "Start one with: docker compose -f compose.dev.yaml up -d"
        )
        if os.environ.get("REQUIRE_DB"):
            pytest.fail(msg)
        pytest.skip(msg)
    yield conn
    conn.close()


@pytest.fixture
def empty_db_url(admin_conn) -> str:
    """URL (libpq style, as it would appear in DATABASE_URL) of a new empty database."""
    name = f"skku_test_{uuid.uuid4().hex[:12]}"
    admin_conn.execute(f'CREATE DATABASE "{name}"')
    url = make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    yield url
    admin_conn.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


@pytest.fixture
def migrated_db_url(empty_db_url: str) -> str:
    migrate(url=Settings(_env_file=None, database_url=empty_db_url).sqlalchemy_url)
    return empty_db_url


@pytest.fixture
def engine(migrated_db_url: str):
    eng = create_engine(Settings(_env_file=None, database_url=migrated_db_url).sqlalchemy_url)
    yield eng
    eng.dispose()


# The read-API tests pin "today" so deadline rules do not depend on when they run.
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=KST)


def _client(url: str) -> TestClient:
    app = create_app(make_settings(database_url=url))
    app.dependency_overrides[get_now] = lambda: NOW
    return TestClient(app)


@pytest.fixture(scope="session")
def seeded_db_url(admin_conn) -> str:
    """One migrated + seeded database shared by the (read-only) API tests."""
    name = f"skku_test_{uuid.uuid4().hex[:12]}"
    admin_conn.execute(f'CREATE DATABASE "{name}"')
    url = make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    sa_url = Settings(_env_file=None, database_url=url).sqlalchemy_url
    migrate(url=sa_url)
    eng = create_engine(sa_url)
    with Session(eng) as session, session.begin():
        seed_dev.seed(session)
    eng.dispose()
    yield url
    admin_conn.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


@pytest.fixture(scope="session")
def api(seeded_db_url):
    """Client for the seeded dataset (36 articles, 7 categories), clock fixed at NOW."""
    with _client(seeded_db_url) as client:
        yield client


@pytest.fixture
def empty_api(migrated_db_url):
    """Client for a migrated but empty database."""
    with _client(migrated_db_url) as client:
        yield client
