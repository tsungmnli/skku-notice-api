"""Migrations 0006 and 0007: sources. The expand step (0006) must not change what existing data or
the previous release see: every article lands on the campus board and old-style inserts still
work. The contract step (0007) drops the old UNIQUE(article_no)."""

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.migrate import alembic_config

CAMPUS_BOARD_URL = "https://www.skku.edu/skku/campus/skk_comm/notice01.do"
INSERT_ARTICLE = (
    "INSERT INTO articles (article_no, title, writer, posted_on, url) "
    "VALUES (:no, 't', 'w', '2026-10-01', 'u')"
)


def sqlalchemy_url(database_url: str) -> str:
    return Settings(_env_file=None, database_url=database_url).sqlalchemy_url


def test_the_campus_board_is_source_1(engine):
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT s.slug, s.level, s.list_url, s.enabled, d.name AS department "
                "FROM sources s JOIN departments d ON d.code = s.department_code WHERE s.id = 1"
            )
        ).one()
    assert (row.slug, row.level, row.list_url, row.enabled) == (
        "skku",
        "all",
        CAMPUS_BOARD_URL,
        True,
    )
    assert row.department == "성균관대학교"


def test_existing_articles_move_to_source_1(empty_db_url):
    url = sqlalchemy_url(empty_db_url)
    command.upgrade(alembic_config(url), "0005")
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text(INSERT_ARTICLE), {"no": 1})
        command.upgrade(alembic_config(url), "head")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT source_id FROM articles")).scalar_one() == 1
    finally:
        engine.dispose()


def test_an_insert_that_does_not_name_source_id_still_works(engine):
    """What the previous release does: it has never heard of the column."""
    with engine.begin() as conn:
        conn.execute(text(INSERT_ARTICLE), {"no": 7})
        row = conn.execute(text("SELECT source_id FROM articles WHERE article_no = 7"))
        assert row.scalar_one() == 1


def test_only_the_per_board_key_is_unique(engine):
    """0007 dropped UNIQUE(article_no); the key is now (source_id, article_no)."""
    names = {u["name"] for u in inspect(engine).get_unique_constraints("articles")}
    assert "uq_articles_article_no" not in names
    assert "uq_articles_source_id_article_no" in names


def test_two_boards_may_use_the_same_number_but_one_board_may_not(engine):
    insert = (
        "INSERT INTO articles (source_id, article_no, title, writer, posted_on, url) "
        "VALUES (:src, :no, 't', 'w', '2026-10-01', 'u')"
    )
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO departments (code, name) VALUES ('sw', '소프트웨어학과')"))
        conn.execute(
            text(
                "INSERT INTO sources (slug, department_code, name, list_url, level) "
                "VALUES ('sw', 'sw', '공지사항', 'https://x.skku.edu/n.do', 'undergrad')"
            )
        )
        conn.execute(text(insert), {"src": 1, "no": 5})
        conn.execute(text(insert), {"src": 2, "no": 5})  # same number, other board: fine
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text(insert), {"src": 2, "no": 5})


def test_0007_can_be_undone_while_numbers_are_still_unique(migrated_db_url):
    url = sqlalchemy_url(migrated_db_url)
    engine = create_engine(url)
    try:
        command.downgrade(alembic_config(url), "0006")
        names = {u["name"] for u in inspect(engine).get_unique_constraints("articles")}
        assert {"uq_articles_article_no", "uq_articles_source_id_article_no"} <= names
        command.upgrade(alembic_config(url), "head")
    finally:
        engine.dispose()


def test_a_new_source_gets_the_next_id(engine):
    """The campus row was inserted with id 1; the sequence must already be past it."""
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO departments (code, name) VALUES ('sw', '소프트웨어학과')"))
        new_id = conn.execute(
            text(
                "INSERT INTO sources (slug, department_code, name, list_url, level) "
                "VALUES ('sw', 'sw', '공지사항', 'https://x.skku.edu/n.do', 'undergrad') "
                "RETURNING id"
            )
        ).scalar_one()
    assert new_id == 2


def test_a_source_needs_a_known_level(engine):
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO sources (slug, department_code, name, list_url, level) "
                "VALUES ('x', 'skku', 'x', 'u', 'masters')"
            )
        )


def test_downgrade_to_0005_keeps_the_articles(migrated_db_url):
    url = sqlalchemy_url(migrated_db_url)
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text(INSERT_ARTICLE), {"no": 3})
        command.downgrade(alembic_config(url), "0005")
        inspector = inspect(engine)
        assert "sources" not in inspector.get_table_names()
        assert "source_id" not in {c["name"] for c in inspector.get_columns("articles")}
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM articles")).scalar_one() == 1
    finally:
        engine.dispose()
