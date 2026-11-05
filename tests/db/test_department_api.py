"""GET /departments, and which boards /articles, /categories and /writers read from.

Nothing picked = the campus-wide board only; picked departments and boards are a union."""

from datetime import date

import pytest
from sqlalchemy import inspect, select, update
from sqlalchemy.orm import Session

from app import queries
from app.crawler.sources import DEPARTMENTS, SOURCES, sync_sources
from app.crawler.store import upsert_categories
from app.models import Article, Category, Source
from tests.db.conftest import NOW

URL = "/api/v1"


@pytest.fixture
def synced(empty_api, engine):
    """An empty database whose boards are registered (what `--sync-sources` does)."""
    with Session(engine) as s, s.begin():
        sync_sources(s)
    return empty_api


def put(engine, slug: str, *numbers: int, category: str | None = None, writer="작성자") -> None:
    """Articles with the given post numbers on the board `slug`."""
    with Session(engine) as s, s.begin():
        upsert_categories(s)
        source_id = s.scalar(select(Source.id).where(Source.slug == slug))
        cats = [s.get(Category, category)] if category else []
        for no in numbers:
            s.add(
                Article(
                    source_id=source_id,
                    article_no=no,
                    title=f"{slug} {no}",
                    writer=writer,
                    posted_on=date(2026, 1, 1),
                    url=f"https://example.test/{slug}/{no}",
                    categories=cats,
                )
            )


def titles(res) -> list[str]:
    return sorted(i["title"] for i in res.json()["items"])


# ---- /departments ------------------------------------------------------------------------


def test_before_a_sync_there_is_nothing_to_pick(empty_api):
    res = empty_api.get(f"{URL}/departments")
    assert res.status_code == 200
    assert res.json() == []  # the campus-wide board is not a department's


def test_the_campus_board_is_never_offered_for_picking(synced):
    body = synced.get(f"{URL}/departments").json()
    assert "skku" not in [d["code"] for d in body]
    assert "skku" not in [s["slug"] for d in body for s in d["sources"]]


def test_departments_come_in_registry_order_with_their_boards(synced):
    body = synced.get(f"{URL}/departments").json()
    assert [d["code"] for d in body] == [d.code for d in DEPARTMENTS if d.code != "skku"]
    sco = next(d for d in body if d["code"] == "sco")
    assert [s["slug"] for s in sco["sources"]] == [s.slug for s in SOURCES if s.department == "sco"]
    assert sco["sources"][0] == {
        "slug": "sco-notice",
        "name": "공지사항",
        "level": "undergrad",
        "count": 0,
    }


def test_counts_are_per_board_and_summed_per_department(synced, engine):
    put(engine, "sw", 1, 2, 3, 4)
    put(engine, "sco-notice", 1)
    put(engine, "sco-data", 1, 2)  # the same numbers as on another board are fine
    body = {d["code"]: d for d in synced.get(f"{URL}/departments").json()}
    assert body["sw"]["count"] == 4
    sco = body["sco"]
    assert sco["count"] == 3
    assert {s["slug"]: s["count"] for s in sco["sources"]} == {
        "sco-notice": 1,
        "sco-data": 2,
        "sco-ai": 0,
        "sco-culture": 0,
        "sco-magnetic": 0,
    }


def test_a_disabled_board_is_left_out_and_so_is_a_department_with_none_left(synced, engine):
    with Session(engine) as s, s.begin():
        s.execute(update(Source).where(Source.slug == "sco-ai").values(enabled=False))
        s.execute(update(Source).where(Source.department_code == "ai").values(enabled=False))
    body = {d["code"]: d for d in synced.get(f"{URL}/departments").json()}
    assert "ai" not in body
    assert "sco-ai" not in [s["slug"] for s in body["sco"]["sources"]]


def test_departments_are_cached_like_the_other_public_reads(synced):
    res = synced.get(f"{URL}/departments")
    assert res.headers["cache-control"] == "public, max-age=60"


# ---- /articles filters -------------------------------------------------------------------


@pytest.fixture
def boards_with_posts(synced, engine):
    put(engine, "skku", 1)
    put(engine, "sw", 1)  # the same number on two boards
    put(engine, "cse", 2)
    put(engine, "sco-notice", 3)
    put(engine, "sco-data", 4)
    return synced


def test_without_a_filter_only_the_campus_board_is_listed(boards_with_posts):
    res = boards_with_posts.get(f"{URL}/articles")
    assert titles(res) == ["skku 1"]
    assert res.json()["total"] == 1


def test_department_keeps_only_its_boards(boards_with_posts):
    res = boards_with_posts.get(f"{URL}/articles", params={"department": "sco"})
    assert titles(res) == ["sco-data 4", "sco-notice 3"]


def test_picking_a_department_does_not_bring_the_campus_board_along(boards_with_posts):
    res = boards_with_posts.get(f"{URL}/articles", params={"department": "sw"})
    assert titles(res) == ["sw 1"]


def test_source_keeps_only_that_board(boards_with_posts):
    res = boards_with_posts.get(f"{URL}/articles", params={"source": "sco-data"})
    assert titles(res) == ["sco-data 4"]


def test_repeated_values_are_alternatives(boards_with_posts):
    res = boards_with_posts.get(f"{URL}/articles", params={"department": ["sw", "cse"]})
    assert titles(res) == ["cse 2", "sw 1"]
    res = boards_with_posts.get(f"{URL}/articles", params={"source": ["skku", "sco-data"]})
    assert titles(res) == ["sco-data 4", "skku 1"]


def test_department_and_source_together_are_a_union(boards_with_posts):
    """A home department's board and a convergence major's board can be picked together."""
    both = {"department": "cse", "source": "sco-data"}
    assert titles(boards_with_posts.get(f"{URL}/articles", params=both)) == [
        "cse 2",
        "sco-data 4",
    ]
    overlap = {"department": "sco", "source": "sco-data"}
    assert titles(boards_with_posts.get(f"{URL}/articles", params=overlap)) == [
        "sco-data 4",
        "sco-notice 3",
    ]


def test_an_unknown_department_or_source_matches_nothing(boards_with_posts):
    for params in ({"department": "nope"}, {"source": "nope"}):
        assert boards_with_posts.get(f"{URL}/articles", params=params).json()["total"] == 0


# ---- every article says which board it is on ---------------------------------------------


def boards_of(res) -> dict[str, tuple[str, str]]:
    """title -> (source, department) of the listed articles."""
    return {i["title"]: (i["source"], i["department"]) for i in res.json()["items"]}


def test_articles_carry_their_board_and_department(boards_with_posts):
    res = boards_with_posts.get(
        f"{URL}/articles", params={"department": "sco", "source": ["cse", "skku"]}
    )
    assert boards_of(res) == {
        "skku 1": ("skku", "skku"),  # the campus-wide board is its own department
        "cse 2": ("cse", "cse"),
        "sco-notice 3": ("sco-notice", "sco"),
        "sco-data 4": ("sco-data", "sco"),  # one department, several boards
    }


def test_a_single_article_and_a_lookup_carry_them_too(boards_with_posts):
    listed = boards_with_posts.get(f"{URL}/articles", params={"source": "sco-data"})
    article = listed.json()["items"][0]
    one = boards_with_posts.get(f"{URL}/articles/{article['id']}").json()
    assert (one["source"], one["department"]) == ("sco-data", "sco")
    looked = boards_with_posts.post(f"{URL}/articles/lookup", json={"ids": [article["id"]]})
    item = looked.json()["items"][0]
    assert (item["source"], item["department"]) == ("sco-data", "sco")


def test_the_board_is_read_with_the_article_not_one_query_per_post(boards_with_posts, engine):
    """A page of articles from several boards must not lazy-load each post's board."""
    f = queries.ArticleFilters(departments=["sco"], sources=["cse", "skku"])
    with Session(engine) as s:
        rows, _ = queries.list_articles(s, f, page=1, size=20, now=NOW)
        assert len(rows) == 4
        assert all("source" not in inspect(a).unloaded for a in rows)


# ---- /categories and /writers count what the list shows ----------------------------------


@pytest.fixture
def categorised(synced, engine):
    put(engine, "skku", 1, 2, category="academic", writer="학교팀")
    put(engine, "sw", 1, category="academic", writer="SW팀")
    put(engine, "cse", 5, category="general", writer="CSE팀")
    return synced


def counts(res) -> dict[str, int]:
    return {c["code"]: c["count"] for c in res.json() if c["count"]}


def test_category_counts_default_to_the_campus_board(categorised):
    res = categorised.get(f"{URL}/categories")
    assert counts(res) == {"academic": 2}
    assert len(res.json()) == 7  # categories without posts are still listed


def test_category_counts_follow_the_picked_boards(categorised):
    assert counts(categorised.get(f"{URL}/categories", params={"department": "sw"})) == {
        "academic": 1
    }
    both = {"department": "sw", "source": "cse"}
    assert counts(categorised.get(f"{URL}/categories", params=both)) == {
        "academic": 1,
        "general": 1,
    }


def test_the_category_count_equals_the_list_total(categorised):
    for params in ({}, {"department": "sw"}, {"source": ["cse", "skku"]}):
        total = categorised.get(f"{URL}/articles", params={**params, "category": "academic"})
        listed = counts(categorised.get(f"{URL}/categories", params=params)).get("academic", 0)
        assert total.json()["total"] == listed


def test_writers_default_to_the_campus_board_and_follow_the_picked_boards(categorised):
    def names(**params):
        return [w["writer"] for w in categorised.get(f"{URL}/writers", params=params).json()]

    assert names() == ["학교팀"]
    assert names(department="sw") == ["SW팀"]
    assert sorted(names(department="sw", source="cse")) == ["CSE팀", "SW팀"]


def test_the_filters_combine_with_the_old_ones(boards_with_posts, engine):
    put(engine, "sco-data", 5)
    res = boards_with_posts.get(
        f"{URL}/articles", params={"department": "sco", "q": "data", "sort": "posted_asc"}
    )
    assert titles(res) == ["sco-data 4", "sco-data 5"]
