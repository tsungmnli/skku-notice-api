from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Article, Category, article_categories
from scripts import seed_dev

KST = timezone(timedelta(hours=9))


def run_seed(engine) -> None:
    with Session(engine) as session, session.begin():
        seed_dev.seed(session)


def counts(engine) -> dict[str, int]:
    with Session(engine) as s:
        return {
            "articles": s.scalar(select(func.count()).select_from(Article)),
            "categories": s.scalar(select(func.count()).select_from(Category)),
            "links": s.scalar(select(func.count()).select_from(article_categories)),
        }


def test_seed_matches_the_mock_dataset(engine):
    run_seed(engine)

    with Session(engine) as s:
        articles = s.scalars(select(Article)).all()
        by_no = {a.article_no: a for a in articles}

        assert len(articles) == 36
        assert s.scalar(select(func.count()).select_from(Category)) == 7
        assert sum(a.is_pinned for a in articles) == 6
        assert sum(a.deadline_at is not None for a in articles) == 29

        # Categories come back in the display order, not insertion order.
        assert [c.code for c in by_no[1000].categories] == ["academic"]
        assert [c.code for c in by_no[1001].categories] == ["admission", "scholarship"]

        # The mock produced an invalid "2026-10-36"; the seed rolls over to Nov 5.
        assert by_no[1018].deadline_at == datetime(2026, 11, 5, 23, 59, tzinfo=KST)
        # A deadline with a time of day, and a date-only one.
        assert by_no[1001].deadline_at == datetime(2026, 10, 6, 17, 0, tzinfo=KST)
        assert by_no[1002].deadline_at.astimezone(KST).strftime("%H:%M") == "23:59"


def test_seed_is_idempotent(engine):
    run_seed(engine)
    with Session(engine) as s:
        ids_before = dict(s.execute(select(Article.article_no, Article.id)).all())
    first = counts(engine)

    run_seed(engine)

    assert counts(engine) == first
    with Session(engine) as s:
        assert dict(s.execute(select(Article.article_no, Article.id)).all()) == ids_before


def test_seed_refuses_to_run_in_prod(monkeypatch):
    for key, value in {
        "APP_ENV": "prod",
        "DATABASE_URL": "postgresql://x:y@127.0.0.1:1/z",
        "PUBLIC_URL": "https://example.com",
        "CORS_ORIGINS": "https://example.com",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit, match="prod"):
            seed_dev.main()
    finally:
        get_settings.cache_clear()
