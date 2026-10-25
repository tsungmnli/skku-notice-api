"""Load the web app's mock dataset into the database: `python -m scripts.seed_dev`.

Development only. It is a port of `lib/mock/data.ts` (36 articles, 7 categories) so the web
app looks the same against the real API before the crawler exists. Safe to re-run: rows are
upserted by `article_no` / `code`.
"""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import make_engine
from app.models import Article, Category, article_categories

KST = timezone(timedelta(hours=9))

WRITERS = [
    "교무팀",
    "학사지원팀",
    "입학처",
    "학생지원팀",
    "산학협력단",
    "장학팀",
    "취업지원팀",
    "국제처",
    "연구지원팀",
    "총학생회",
]

CATEGORIES = [
    ("academic", "학사"),
    ("admission", "입학"),
    ("career", "취업"),
    ("recruit", "채용·모집"),
    ("scholarship", "장학"),
    ("event", "행사·세미나"),
    ("general", "일반"),
]

TITLES = [
    "학사일정 변경 안내",
    "입학설명회 개최 안내",
    "채용 정보 업데이트",
    "취업 박람회 참가 신청",
    "장학금 신청 안내",
    "학술 세미나 개최",
    "교내 행사 참가 모집",
    "시험 응시 규정 안내",
    "연구 과제 공고",
    "국제 교류 프로그램 모집",
]

COUNT = 36


def _pick_categories(seed: int) -> list[str]:
    codes = [c for c, _ in CATEGORIES]
    result: list[str] = []
    for k in range((seed % 3) + 1):
        code = codes[(seed + k * 3) % len(codes)]
        if code not in result:
            result.append(code)
    return result


def _deadline(i: int) -> datetime | None:
    """Same pattern as the mock: ~80% have one; some passed, some date-only (23:59)."""

    # Day numbers can run past the month's end (the mock produced "2026-10-36"); roll over.
    def at(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
        d = date(year, month, 1) + timedelta(days=day - 1)
        return datetime(d.year, d.month, d.day, hour, minute, tzinfo=KST)

    match i % 5:
        case 0:  # already passed
            return at(2026, 2, (i % 10) + 1, 23, 59)
        case 1:  # with a time of day
            return at(2026, 10, (i % 20) + 5, 17, 0)
        case 2:  # date only
            return at(2026, 11, (i % 20) + 10, 23, 59)
        case 3:  # near future
            return at(2026, 10, (i % 20) + 18, 23, 59)
        case _:
            return None


def make_article(i: int) -> dict:
    article_no = 1000 + i
    return {
        "article_no": article_no,
        "title": f"{TITLES[i % len(TITLES)]} ({article_no})",
        "writer": WRITERS[i % len(WRITERS)],
        "posted_on": date(2026, (i % 9) + 1, (i % 27) + 1),
        "display_no": article_no if i % 2 == 0 else None,
        "is_pinned": i % 7 == 0,
        "view_count": ((i * 37) % 5000) + 50,
        "url": f"https://www.skku.edu/skku/campus/skk_community_1.do?article_no={article_no}",
        "deadline_at": _deadline(i),
    }


def seed(session: Session) -> None:
    """Upsert the dataset. The caller commits."""
    cat_stmt = pg_insert(Category).values(
        [{"code": c, "name": n, "sort_order": order} for order, (c, n) in enumerate(CATEGORIES)]
    )
    session.execute(
        cat_stmt.on_conflict_do_update(
            index_elements=[Category.code],
            set_={"name": cat_stmt.excluded.name, "sort_order": cat_stmt.excluded.sort_order},
        )
    )

    rows = [make_article(i) for i in range(COUNT)]
    stmt = pg_insert(Article).values(rows)
    id_by_no = dict(
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=[Article.article_no],
                set_={col: getattr(stmt.excluded, col) for col in rows[0] if col != "article_no"},
            ).returning(Article.article_no, Article.id)
        ).all()
    )

    # Re-link categories from scratch so a re-run converges to the same state.
    session.execute(
        delete(article_categories).where(article_categories.c.article_id.in_(id_by_no.values()))
    )
    session.execute(
        insert(article_categories),
        [
            {"article_id": id_by_no[row["article_no"]], "category_code": code}
            for i, row in enumerate(rows)
            for code in _pick_categories(i)
        ],
    )


def main() -> None:
    settings = get_settings()
    if settings.app_env == "prod":
        raise SystemExit("Refusing to load development data with APP_ENV=prod.")
    engine = make_engine(settings.sqlalchemy_url)
    with Session(engine) as session, session.begin():
        seed(session)
    engine.dispose()
    print(f"Seeded {COUNT} articles and {len(CATEGORIES)} categories.")


if __name__ == "__main__":
    main()
