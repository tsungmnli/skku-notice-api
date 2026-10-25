"""Write crawled articles to the database. Idempotent: running twice changes nothing."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, insert, literal_column, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.crawler.categories import CATEGORIES
from app.crawler.parser import ListedArticle
from app.models import Article, Category, article_categories

# Columns the crawler owns. `deadline_at` is handled separately (never erased by a None).
_COLUMNS = ("title", "writer", "posted_on", "display_no", "is_pinned", "view_count", "url")


@dataclass(frozen=True)
class StoreResult:
    inserted: int
    updated: int
    unchanged: int
    unpinned: int


def upsert_categories(session: Session) -> None:
    """The 7 categories must exist before articles link to them (FK). Prod has no seed."""
    stmt = pg_insert(Category).values(
        [{"code": c, "name": n, "sort_order": i} for i, (c, n) in enumerate(CATEGORIES)]
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=[Category.code],
            set_={"name": stmt.excluded.name, "sort_order": stmt.excluded.sort_order},
        )
    )


def merge_duplicates(items: list[ListedArticle]) -> list[ListedArticle]:
    """One entry per article_no. A post can show up pinned and again in the regular list."""
    merged: dict[int, ListedArticle] = {}
    for item in items:
        prev = merged.get(item.article_no)
        if prev is None:
            merged[item.article_no] = item
            continue
        pinned = prev.is_pinned or item.is_pinned
        display_no = prev.display_no if prev.display_no is not None else item.display_no
        merged[item.article_no] = ListedArticle(
            **{**prev.__dict__, "is_pinned": pinned, "display_no": display_no}
        )
    return list(merged.values())


def upsert_articles(
    session: Session,
    items: list[ListedArticle],
    deadlines: dict[int, datetime | None],
    *,
    sync_pinned: bool = False,
) -> StoreResult:
    """Insert new articles, update changed ones, relink categories. The caller commits.

    `sync_pinned=True` means `items` includes the first list page, the only place pinned posts
    are guaranteed to appear: articles pinned in the DB but not pinned in `items` get unpinned.
    """
    items = merge_duplicates(items)
    if not items:
        return StoreResult(0, 0, 0, 0)

    rows = [
        {
            "article_no": it.article_no,
            **{col: getattr(it, col) for col in _COLUMNS},
            "deadline_at": deadlines.get(it.article_no),
        }
        for it in items
    ]
    stmt = pg_insert(Article).values(rows)
    new_deadline = func.coalesce(stmt.excluded.deadline_at, Article.deadline_at)
    changed = or_(
        *(getattr(Article, c).is_distinct_from(getattr(stmt.excluded, c)) for c in _COLUMNS),
        Article.deadline_at.is_distinct_from(new_deadline),
    )
    # `xmax = 0` is how Postgres tells a freshly inserted row from an updated one.
    result = session.execute(
        stmt.on_conflict_do_update(
            index_elements=[Article.article_no],
            set_={
                **{c: getattr(stmt.excluded, c) for c in _COLUMNS},
                "deadline_at": new_deadline,
                "updated_at": func.now(),
            },
            # Rows that did not change are skipped entirely (no write, no updated_at bump).
            where=changed,
        ).returning(Article.article_no, literal_column("(articles.xmax = 0)").label("inserted"))
    ).all()
    inserted = sum(1 for r in result if r.inserted)
    updated = len(result) - inserted

    _link_categories(session, items)

    unpinned = 0
    if sync_pinned:
        pinned_nos = [it.article_no for it in items if it.is_pinned]
        unpinned = session.execute(
            update(Article)
            .where(Article.is_pinned.is_(True), Article.article_no.not_in(pinned_nos))
            .values(is_pinned=False, updated_at=func.now())
        ).rowcount
    return StoreResult(inserted, updated, len(items) - len(result), unpinned)


def _link_categories(session: Session, items: list[ListedArticle]) -> None:
    """Make article_categories match the board's label, touching only rows that differ."""
    id_by_no = dict(
        session.execute(
            select(Article.article_no, Article.id).where(
                Article.article_no.in_([it.article_no for it in items])
            )
        ).all()
    )
    ids = list(id_by_no.values())
    current: dict[int, set[str]] = {}
    for article_id, code in session.execute(
        select(article_categories.c.article_id, article_categories.c.category_code).where(
            article_categories.c.article_id.in_(ids)
        )
    ):
        current.setdefault(article_id, set()).add(code)

    stale: list[int] = []
    missing: list[dict[str, object]] = []
    for it in items:
        article_id = id_by_no[it.article_no]
        have = current.get(article_id, set())
        if have == {it.category_code}:
            continue
        if have:
            stale.append(article_id)
        missing.append({"article_id": article_id, "category_code": it.category_code})
    if stale:
        session.execute(
            delete(article_categories).where(article_categories.c.article_id.in_(stale))
        )
    if missing:
        session.execute(insert(article_categories), missing)
