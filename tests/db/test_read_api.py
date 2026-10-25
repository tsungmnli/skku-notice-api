"""Contract tests for the v1 read endpoints (see docs/api-contract.md)."""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.models import Article, Category
from tests.db import reference
from tests.db.conftest import NOW

URL = "/api/v1"
ROWS = reference.dataset()


def add_articles(engine, *specs: dict) -> None:
    """Insert articles (and the categories they need) with sensible defaults."""
    with Session(engine) as s, s.begin():
        for n, spec in enumerate(specs, start=1):
            s.add(
                Article(
                    **{
                        "article_no": 5000 + n,
                        "title": f"글 {n}",
                        "writer": "작성자",
                        "posted_on": date(2026, 1, 1),
                        "url": f"https://example.test/{n}",
                        **spec,
                    }
                )
            )


# ---- shape -------------------------------------------------------------------------------


def test_article_json_matches_types_ts(api):
    res = api.get(f"{URL}/articles/2")
    assert res.status_code == 200
    assert res.json() == {
        "id": 2,
        "article_no": 1001,
        "title": "입학설명회 개최 안내 (1001)",
        "writer": "학사지원팀",
        "posted_on": "2026-02-02",
        "display_no": None,
        "is_pinned": False,
        "view_count": 87,
        "url": "https://www.skku.edu/skku/campus/skk_community_1.do?article_no=1001",
        # Category codes, in the display order of the filter UI.
        "categories": ["admission", "scholarship"],
        "deadline_at": "2026-10-06T17:00:00+09:00",
    }


def test_list_envelope_and_item_keys(api):
    body = api.get(f"{URL}/articles", params={"size": 3}).json()
    assert list(body) == ["items", "total", "page", "size", "has_next"]
    assert set(body["items"][0]) == {
        "id", "article_no", "title", "writer", "posted_on", "display_no",
        "is_pinned", "view_count", "url", "categories", "deadline_at",
    }  # fmt: skip
    assert (body["total"], body["page"], body["size"], body["has_next"]) == (36, 1, 3, True)


def test_deadline_is_always_rendered_in_seoul_time(empty_api, engine):
    # Stored as an instant in UTC; the contract wants `+09:00`, never `Z`.
    add_articles(engine, {"deadline_at": datetime(2026, 10, 6, 8, 0, tzinfo=UTC)})
    got = empty_api.get(f"{URL}/articles").json()["items"][0]["deadline_at"]
    assert got == "2026-10-06T17:00:00+09:00"


# ---- errors ------------------------------------------------------------------------------


def test_get_article_404_and_bad_ids(api):
    missing = api.get(f"{URL}/articles/9999")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "게시글을 찾을 수 없습니다."}
    # Larger than a 32-bit id: must be "not found", not a database error.
    assert api.get(f"{URL}/articles/99999999999").status_code == 404
    assert api.get(f"{URL}/articles/0").status_code == 404
    assert api.get(f"{URL}/articles/abc").status_code == 422


@pytest.mark.parametrize(
    ("params", "message"),
    [
        (
            {"date_from": "2026-05-01", "date_to": "2026-01-01"},
            "date_from은 date_to보다 이후일 수 없습니다.",
        ),
        (
            {"deadline_from": "2026-05-01", "deadline_to": "2026-01-01"},
            "deadline_from은 deadline_to보다 이후일 수 없습니다.",
        ),
    ],
)
def test_reversed_ranges_are_422_with_the_contract_message(api, params, message):
    res = api.get(f"{URL}/articles", params=params)
    assert res.status_code == 422
    assert res.json() == {"detail": message}


@pytest.mark.parametrize(
    "params",
    [
        {"sort": "nope"},
        {"pinned": "maybe"},
        {"page": 0},
        {"date_from": "tomorrow"},
        {"q": "x" * 101},
    ],
)
def test_invalid_query_values_are_422_with_a_single_detail_string(api, params):
    res = api.get(f"{URL}/articles", params=params)
    assert res.status_code == 422
    assert list(res.json()) == ["detail"]
    assert isinstance(res.json()["detail"], str)


def test_equal_range_ends_are_allowed(api):
    res = api.get(f"{URL}/articles", params={"date_from": "2026-02-02", "date_to": "2026-02-02"})
    assert res.status_code == 200


# ---- deadline boundaries (seed data has no row on these edges) -----------------------------


def test_deadline_range_is_by_seoul_calendar_day(empty_api, engine):
    kst = timezone(timedelta(hours=9))
    add_articles(
        engine,
        {"title": "10/25 00:00", "deadline_at": datetime(2026, 10, 25, 0, 0, tzinfo=kst)},
        {"title": "10/25 23:59", "deadline_at": datetime(2026, 10, 25, 23, 59, tzinfo=kst)},
        {"title": "10/26 00:00", "deadline_at": datetime(2026, 10, 26, 0, 0, tzinfo=kst)},
        {"title": "10/24 23:59", "deadline_at": datetime(2026, 10, 24, 23, 59, tzinfo=kst)},
        {"title": "none"},
    )
    res = empty_api.get(
        f"{URL}/articles",
        params={"deadline_from": "2026-10-25", "deadline_to": "2026-10-25", "sort": "deadline_asc"},
    ).json()
    # Midnight that *starts* the 26th belongs to the 26th, not the 25th.
    assert [a["title"] for a in res["items"]] == ["10/25 00:00", "10/25 23:59"]


def test_deadline_asc_cut_is_seoul_midnight_not_utc(empty_api, engine):
    kst = timezone(timedelta(hours=9))
    # NOW is 2026-10-24 12:00 KST. 00:30 KST on the 24th is 15:30 UTC on the 23rd.
    add_articles(
        engine,
        {"title": "today early", "deadline_at": datetime(2026, 10, 24, 0, 30, tzinfo=kst)},
        {"title": "yesterday late", "deadline_at": datetime(2026, 10, 23, 23, 59, tzinfo=kst)},
    )
    res = empty_api.get(f"{URL}/articles", params={"sort": "deadline_asc"}).json()
    assert [a["title"] for a in res["items"]] == ["today early"]


def test_search_treats_like_wildcards_literally(empty_api, engine):
    add_articles(engine, {"title": "100% 지원"}, {"title": "100 점 만점"}, {"title": "a_b"})
    titles = lambda q: [  # noqa: E731
        a["title"] for a in empty_api.get(f"{URL}/articles", params={"q": q}).json()["items"]
    ]
    assert titles("%") == ["100% 지원"]
    assert titles("_") == ["a_b"]
    assert titles("\\") == []


def test_paging_is_stable_when_sort_keys_tie(empty_api, engine):
    # Same date and same views for everyone: only the `id` tie-break keeps pages consistent.
    add_articles(engine, *({"view_count": 10} for _ in range(7)))
    for sort in ("posted_desc", "posted_asc", "views_desc"):
        seen: list[int] = []
        for page in (1, 2, 3):
            seen += [
                a["id"]
                for a in empty_api.get(
                    f"{URL}/articles", params={"sort": sort, "size": 3, "page": page}
                ).json()["items"]
            ]
        assert sorted(seen) == list(range(1, 8)), sort


def test_clock_default_is_the_real_time(migrated_db_url):
    """Without the test override the endpoint still works (uses the real Seoul clock)."""
    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.conftest import make_settings

    with TestClient(create_app(make_settings(database_url=migrated_db_url))) as c:
        assert c.get(f"{URL}/articles", params={"sort": "deadline_asc"}).status_code == 200


# ---- lookup ------------------------------------------------------------------------------


def test_lookup_keeps_request_order_dedupes_and_reports_missing(api):
    res = api.post(f"{URL}/articles/lookup", json={"ids": [5, 999, 2, 5, 999, 1]})
    assert res.status_code == 200
    body = res.json()
    assert [a["id"] for a in body["items"]] == [5, 2, 1]
    assert body["missing_ids"] == [999]


def test_lookup_ids_outside_the_id_range_are_just_missing(api):
    res = api.post(f"{URL}/articles/lookup", json={"ids": [99999999999, 0, -4, 3]})
    assert res.status_code == 200
    assert [a["id"] for a in res.json()["items"]] == [3]
    assert res.json()["missing_ids"] == [99999999999, 0, -4]


@pytest.mark.parametrize("count", [0, 201])
def test_lookup_count_limits(api, count):
    res = api.post(f"{URL}/articles/lookup", json={"ids": list(range(1, count + 1))})
    assert res.status_code == 422
    assert res.json() == {"detail": "조회할 ID는 1~200개까지 가능합니다."}


def test_lookup_accepts_exactly_200_ids(api):
    res = api.post(f"{URL}/articles/lookup", json={"ids": list(range(1, 201))})
    assert res.status_code == 200
    assert len(res.json()["items"]) == 36
    assert len(res.json()["missing_ids"]) == 164


def test_lookup_rejects_a_malformed_body(api):
    for body in ({}, {"ids": "1,2"}, {"ids": ["a"]}):
        res = api.post(f"{URL}/articles/lookup", json=body)
        assert res.status_code == 422
        assert list(res.json()) == ["detail"]


# ---- categories --------------------------------------------------------------------------


def test_categories_in_display_order_with_counts(api):
    got = api.get(f"{URL}/categories").json()
    assert [c["code"] for c in got] == reference.CATEGORY_ORDER
    assert [c["name"] for c in got] == [n for _, n in reference.seed_dev.CATEGORIES]
    want = {code: sum(code in a["categories"] for a in ROWS) for code in reference.CATEGORY_ORDER}
    assert {c["code"]: c["count"] for c in got} == want
    assert all(c["count"] > 0 for c in got)


def test_categories_without_articles_are_listed_with_zero(empty_api, engine):
    with Session(engine) as s, s.begin():
        s.add_all(
            [Category(code="b", name="B", sort_order=1), Category(code="a", name="A", sort_order=0)]
        )
    assert empty_api.get(f"{URL}/categories").json() == [
        {"code": "a", "name": "A", "count": 0},
        {"code": "b", "name": "B", "count": 0},
    ]


def test_empty_database_returns_empty_collections(empty_api):
    assert empty_api.get(f"{URL}/categories").json() == []
    assert empty_api.get(f"{URL}/writers").json() == []
    body = empty_api.get(f"{URL}/articles").json()
    assert body == {"items": [], "total": 0, "page": 1, "size": 20, "has_next": False}


# ---- writers -----------------------------------------------------------------------------


def test_writers_sorted_by_count_then_name(empty_api, engine):
    add_articles(
        engine,
        *({"writer": w} for w in ["b", "b", "a", "a", "c", "c", "c", "d"]),
    )
    assert empty_api.get(f"{URL}/writers").json() == [
        {"writer": "c", "count": 3},
        {"writer": "a", "count": 2},
        {"writer": "b", "count": 2},
        {"writer": "d", "count": 1},
    ]


def test_writers_counts_on_the_seed(api):
    got = api.get(f"{URL}/writers").json()
    assert sum(w["count"] for w in got) == 36
    assert len(got) == 10
    counts = [w["count"] for w in got]
    assert counts == sorted(counts, reverse=True)


def test_writers_q_is_a_substring_match(api):
    assert api.get(f"{URL}/writers", params={"q": "교무"}).json() == [
        {"writer": "교무팀", "count": 4}
    ]
    assert len(api.get(f"{URL}/writers", params={"q": "팀"}).json()) == 6
    assert api.get(f"{URL}/writers", params={"q": "%"}).json() == []
    # Blank q means "no filter".
    assert len(api.get(f"{URL}/writers", params={"q": "  "}).json()) == 10


def test_writers_limit_is_clamped_to_1_200(api):
    assert len(api.get(f"{URL}/writers", params={"limit": 3}).json()) == 3
    assert len(api.get(f"{URL}/writers", params={"limit": 0}).json()) == 1
    assert len(api.get(f"{URL}/writers", params={"limit": 5000}).json()) == 10


def test_fixed_clock_is_the_test_clock(api):
    # Sanity: the shared client really runs at NOW, which the parity expectations depend on.
    res = api.get(f"{URL}/articles", params={"sort": "deadline_asc", "size": 50}).json()
    assert min(a["deadline_at"] for a in res["items"]) >= NOW.date().isoformat()
