"""NEW/UPDATED support: read marks (`/me/reads`), their housekeeping, `content_changed_at`.

The clock is pinned to 2026-11-05 12:00 KST, so the display window is 10-30 .. 11-05 (7 Seoul
days, today included). Articles are placed on both sides of every edge of it.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.auth.cleanup import purge_stale_reads
from app.auth.sessions import create_session
from app.freshness import in_window, past_window, window_start
from app.main import create_app
from app.models import Article, User, UserArticleRead
from app.timeutil import KST, get_now
from tests.conftest import make_settings

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=KST)
PATH = "/api/v1/me/reads"


def day(month: int, d: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, month, d, hour, minute, tzinfo=KST)


# name -> (posted_on, content_changed_at). Window starts 10-30 00:00 KST.
ARTICLES = {
    "fresh": ("2026-11-04", None),  # NEW
    "first_day": ("2026-10-30", None),  # posted on the first day of the window: still in
    "just_out": ("2026-10-29", None),  # one day too old
    "old": ("2026-10-01", None),
    "old_edited_today": ("2026-10-01", NOW - timedelta(hours=1)),  # edited: lives again
    "edited_first_day": ("2026-10-01", day(10, 30, 0, 0)),  # edit on the first moment: in
    "edited_just_out": ("2026-10-01", day(10, 29, 23, 59)),  # edit one minute too early
}


@pytest.fixture
def clock():
    return {"now": NOW}


@pytest.fixture
def served(migrated_db_url, clock):
    app = create_app(make_settings(database_url=migrated_db_url))
    app.dependency_overrides[get_now] = lambda: clock["now"]
    with TestClient(app) as client:
        yield app, client


@pytest.fixture
def ids(served) -> dict[str, int]:
    app, _ = served
    out: dict[str, int] = {}
    with app.state.sessionmaker() as s, s.begin():
        for n, (name, (posted, changed)) in enumerate(ARTICLES.items(), start=1):
            a = Article(
                article_no=n,
                title=name,
                writer="w",
                posted_on=datetime.fromisoformat(posted).date(),
                url=f"https://example.test/{n}",
                content_changed_at=changed,
            )
            s.add(a)
            s.flush()
            out[name] = a.id
    return out


def make_user(app, email: str = "kim@example.com") -> dict[str, str]:
    """A verified user with a Bearer session; returns the Authorization header."""
    with app.state.sessionmaker() as s, s.begin():
        user = User(email=email, email_verified_at=NOW)
        s.add(user)
        s.flush()
        token = create_session(s, user.id, now=NOW, ttl_days=30, client="android")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def auth(served) -> dict[str, str]:
    return make_user(served[0])


def mark(client, auth, *items, **kw):
    return client.post(PATH, json={"items": list(items)}, headers=auth, **kw)


def reads(client, auth) -> dict[int, str]:
    r = client.get(PATH, headers=auth)
    assert r.status_code == 200
    return {i["article_id"]: i["read_at"] for i in r.json()["items"]}


# --- who may use it ---------------------------------------------------------------------


def test_reads_need_a_login(served):
    _, client = served
    for r in (client.get(PATH), client.post(PATH, json={"items": []})):
        assert r.status_code == 401 and r.json()["code"] == "not_authenticated"


def test_reads_responses_are_never_cached(served, auth):
    _, client = served
    assert client.get(PATH, headers=auth).headers["cache-control"] == "no-store"


# --- marking and listing ----------------------------------------------------------------


def test_marked_articles_come_back_with_the_time_in_seoul(served, auth, ids):
    _, client = served
    r = mark(client, auth, {"article_id": ids["fresh"]}, {"article_id": ids["old_edited_today"]})
    assert r.status_code == 200 and r.json() == {"saved": 2}
    assert reads(client, auth) == {
        ids["fresh"]: "2026-11-05T12:00:00+09:00",
        ids["old_edited_today"]: "2026-11-05T12:00:00+09:00",
    }


@pytest.mark.parametrize(
    ("name", "accepted"),
    [
        ("fresh", True),
        ("first_day", True),
        ("just_out", False),
        ("old", False),
        ("old_edited_today", True),
        ("edited_first_day", True),
        ("edited_just_out", False),
    ],
)
def test_only_articles_inside_the_window_are_accepted(served, auth, ids, name, accepted):
    _, client = served
    r = mark(client, auth, {"article_id": ids[name]})
    assert r.status_code == 200 and r.json() == {"saved": int(accepted)}
    assert (ids[name] in reads(client, auth)) is accepted


def test_unknown_ids_are_skipped_not_an_error(served, auth, ids):
    _, client = served
    r = mark(client, auth, {"article_id": 2_000_000_000}, {"article_id": ids["fresh"]})
    assert r.json() == {"saved": 1}


def test_marking_twice_is_harmless_and_keeps_the_latest_time(served, auth, ids, clock):
    _, client = served
    a = ids["fresh"]
    two_hours_ago = (NOW - timedelta(hours=2)).isoformat()
    three_hours_ago = (NOW - timedelta(hours=3)).isoformat()

    mark(client, auth, {"article_id": a, "read_at": two_hours_ago})
    assert reads(client, auth)[a] == "2026-11-05T10:00:00+09:00"

    # An older mark (an upload that arrives late) never moves it backwards.
    mark(client, auth, {"article_id": a, "read_at": three_hours_ago})
    assert reads(client, auth)[a] == "2026-11-05T10:00:00+09:00"

    # Opening it again later does.
    mark(client, auth, {"article_id": a})
    assert reads(client, auth)[a] == "2026-11-05T12:00:00+09:00"


def test_the_same_id_twice_in_one_request_keeps_the_later_time(served, auth, ids):
    _, client = served
    a = ids["fresh"]
    early = {"article_id": a, "read_at": (NOW - timedelta(hours=5)).isoformat()}
    late = {"article_id": a, "read_at": (NOW - timedelta(hours=1)).isoformat()}
    assert mark(client, auth, early, late).json() == {"saved": 1}
    assert reads(client, auth)[a] == "2026-11-05T11:00:00+09:00"


def test_a_read_time_in_the_future_is_cut_to_now(served, auth, ids):
    _, client = served
    future = (NOW + timedelta(days=1)).isoformat()
    mark(client, auth, {"article_id": ids["fresh"], "read_at": future})
    assert reads(client, auth)[ids["fresh"]] == "2026-11-05T12:00:00+09:00"


def test_uploaded_older_reads_keep_their_own_time(served, auth, ids):
    """Reads kept while signed out are uploaded at login: their time is when the user really
    opened them, which UPDATED compares against the edit."""
    _, client = served
    when = day(11, 2, 9, 30).isoformat()
    mark(client, auth, {"article_id": ids["first_day"], "read_at": when})
    assert reads(client, auth)[ids["first_day"]] == "2026-11-02T09:30:00+09:00"


def test_reads_belong_to_one_account(served, auth, ids):
    app, client = served
    other = make_user(app, "lee@example.com")
    mark(client, auth, {"article_id": ids["fresh"]})
    assert reads(client, other) == {}


def test_reads_listing_drops_what_fell_out_of_the_window(served, auth, ids, clock):
    _, client = served
    mark(client, auth, {"article_id": ids["fresh"]}, {"article_id": ids["old_edited_today"]})
    clock["now"] = NOW + timedelta(days=2)  # 11-07: window 11-01 .. 11-07
    assert set(reads(client, auth)) == {ids["fresh"], ids["old_edited_today"]}
    clock["now"] = NOW + timedelta(days=6)  # 11-11: window 11-05 .. 11-11
    # `fresh` (posted 11-04) is out, the one edited on 11-05 is still in, as the edit says.
    assert set(reads(client, auth)) == {ids["old_edited_today"]}


# --- bad requests -----------------------------------------------------------------------


def test_naive_read_times_are_refused(served, auth, ids):
    _, client = served
    r = mark(client, auth, {"article_id": ids["fresh"], "read_at": "2026-11-05T10:00:00"})
    assert r.status_code == 422


@pytest.mark.parametrize("bad", [0, -1, 2**31, "x"])
def test_bad_ids_are_refused(served, auth, bad):
    _, client = served
    assert mark(client, auth, {"article_id": bad}).status_code == 422


def test_at_most_200_ids_per_request(served, auth, ids):
    _, client = served
    ok = [{"article_id": 1_000_000 + i} for i in range(200)]
    assert mark(client, auth, *ok).status_code == 200
    assert mark(client, auth, *ok, {"article_id": 1}).status_code == 422


def test_an_empty_request_is_fine(served, auth):
    _, client = served
    assert mark(client, auth).json() == {"saved": 0}


def test_marking_is_rate_limited_per_account(served, auth):
    _, client = served
    for _ in range(60):
        assert mark(client, auth).status_code == 200
    r = mark(client, auth)
    assert r.status_code == 429 and "retry-after" in r.headers


# --- housekeeping -----------------------------------------------------------------------


def purge(app, now) -> int:
    with app.state.sessionmaker() as s, s.begin():
        return purge_stale_reads(s, now=now)


def stored(app) -> set[int]:
    with app.state.sessionmaker() as s:
        return set(s.scalars(select(UserArticleRead.article_id)))


def insert_reads(app, article_ids) -> None:
    """Straight into the table, as if the marks had been made earlier."""
    with app.state.sessionmaker() as s, s.begin():
        user_id = s.scalar(select(User.id))
        s.add_all(UserArticleRead(user_id=user_id, article_id=i, read_at=NOW) for i in article_ids)


def test_purge_removes_exactly_what_is_past_the_window(served, auth, ids):
    app, _ = served
    insert_reads(app, ids.values())
    removed = purge(app, NOW)
    assert removed == 3  # old, just_out, edited_just_out
    assert stored(app) == {
        ids["fresh"],
        ids["first_day"],
        ids["old_edited_today"],
        ids["edited_first_day"],
    }


def test_purge_follows_the_notice_not_the_age_of_the_mark(served, auth, ids):
    """Read on day 1, edited on day 5: the mark must outlive 7 days after reading, or the
    notice would come back as UPDATED on day 8."""
    app, _ = served
    insert_reads(app, [ids["old_edited_today"]])  # posted long ago, edited 11-05
    assert purge(app, NOW + timedelta(days=6)) == 0  # 11-11: window 11-05.., edit still counts
    assert stored(app) == {ids["old_edited_today"]}
    assert purge(app, NOW + timedelta(days=7)) == 1  # 11-12: window 11-06.., past it now
    assert stored(app) == set()


def test_window_and_past_window_split_every_article_exactly_once(served, ids):
    """Including articles that were never edited (content_changed_at NULL), where a plain
    NOT(in_window) would be NULL and a DELETE would silently skip them."""
    app, _ = served
    start = window_start(NOW)
    with app.state.sessionmaker() as s:
        inside = s.scalar(select(func.count()).select_from(Article).where(in_window(start)))
        past = s.scalar(select(func.count()).select_from(Article).where(past_window(start)))
        total = s.scalar(select(func.count()).select_from(Article))
    assert (inside, past, total) == (4, 3, len(ARTICLES))


def test_deleting_the_account_or_the_article_removes_the_marks(served, auth, ids):
    app, _ = served
    insert_reads(app, [ids["fresh"], ids["first_day"]])
    with app.state.sessionmaker() as s, s.begin():
        s.execute(text("DELETE FROM articles WHERE id = :i"), {"i": ids["fresh"]})
    assert stored(app) == {ids["first_day"]}
    with app.state.sessionmaker() as s, s.begin():
        s.execute(text("DELETE FROM users"))
    assert stored(app) == set()


# --- the article list ---------------------------------------------------------------------


def test_articles_carry_content_changed_at_in_seoul_time(served, ids):
    _, client = served
    items = {i["title"]: i for i in client.get("/api/v1/articles?size=50").json()["items"]}
    assert items["old_edited_today"]["content_changed_at"] == "2026-11-05T11:00:00+09:00"
    assert items["fresh"]["content_changed_at"] is None
    one = client.get(f"/api/v1/articles/{ids['edited_first_day']}").json()
    assert one["content_changed_at"] == "2026-10-30T00:00:00+09:00"
