from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app.config import Settings
from app.migrate import alembic_config, run
from app.models import Base

APP_TABLES = {
    "articles",
    "categories",
    "article_categories",
    "users",
    "sessions",
    "email_tokens",
    "user_article_reads",
    "departments",
    "sources",
}


def sqlalchemy_url(database_url: str) -> str:
    return Settings(_env_file=None, database_url=database_url).sqlalchemy_url


def table_names(url: str) -> set[str]:
    engine = create_engine(sqlalchemy_url(url))
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_upgrade_creates_the_schema(empty_db_url):
    run(url=sqlalchemy_url(empty_db_url))
    assert table_names(empty_db_url) == APP_TABLES | {"alembic_version"}


def test_models_and_migrations_agree(engine):
    """Fails when a model changed without a new migration (autogenerate would find a diff)."""
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_upgrade_twice_is_a_noop(migrated_db_url):
    run(url=sqlalchemy_url(migrated_db_url))
    assert table_names(migrated_db_url) == APP_TABLES | {"alembic_version"}


def test_0005_leaves_existing_articles_unchanged(empty_db_url):
    """An existing article must not light up UPDATED when 0005 ships: content_changed_at stays
    NULL, and view_count_synced_at starts at the migration time (so views refresh within a day)."""
    url = sqlalchemy_url(empty_db_url)
    command.upgrade(alembic_config(url), "0004")
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO articles (article_no, title, writer, posted_on, url) "
                    "VALUES (1, 't', 'w', '2026-10-01', 'u')"
                )
            )
        command.upgrade(alembic_config(url), "head")
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT content_changed_at, view_count_synced_at FROM articles")
            ).one()
        assert row.content_changed_at is None
        assert row.view_count_synced_at is not None
    finally:
        engine.dispose()


def test_downgrade_removes_everything(migrated_db_url):
    command.downgrade(alembic_config(sqlalchemy_url(migrated_db_url)), "base")
    assert table_names(migrated_db_url) == {"alembic_version"}
