"""A plain-Python reference of the API contract, used as an oracle in parity tests.

It is a port of the web app's `lib/mock/handlers.ts` over the same dataset the seed script
loads, with the deliberate deviations listed in docs/api-contract.md ("mock과 달라지는 점"):
  - `q` matches category *names* (the mock searched the English codes),
  - `has_deadline=false` keeps only articles without a deadline,
  - ties are broken by `id`, so paging is stable.
Keep it naive on purpose: it must be obviously right, not fast.
"""

from datetime import date, datetime

from scripts import seed_dev

KST = seed_dev.KST
CATEGORY_NAMES = dict(seed_dev.CATEGORIES)
CATEGORY_ORDER = [code for code, _ in seed_dev.CATEGORIES]


def dataset() -> list[dict]:
    """The seeded articles as a fresh database numbers them (id = insertion order, from 1)."""
    rows = []
    for i in range(seed_dev.COUNT):
        row = seed_dev.make_article(i)
        row["id"] = i + 1
        row["categories"] = sorted(seed_dev._pick_categories(i), key=CATEGORY_ORDER.index)
        rows.append(row)
    return rows


def _day(dt: datetime) -> date:
    return dt.astimezone(KST).date()


def _matches_q(a: dict, q: str | None) -> bool:
    terms = (q or "").split()
    names = " ".join(CATEGORY_NAMES[c] for c in a["categories"])
    haystack = f"{a['title']} {a['writer']} {names}".lower()
    return all(t.lower() in haystack for t in terms)


def query(rows: list[dict], params: dict, now: datetime) -> dict:
    """Apply the contract to `rows`; `params` uses the API's query names (lists for repeats)."""
    p = params
    cats, writers = p.get("category", []), p.get("writer", [])
    dfrom, dto = p.get("date_from"), p.get("date_to")
    xfrom, xto = p.get("deadline_from"), p.get("deadline_to")
    pinned, has_dl, sort = p.get("pinned", "include"), p.get("has_deadline"), p.get("sort")
    ranged = bool(xfrom or xto)

    def keep(a: dict) -> bool:
        if not _matches_q(a, p.get("q")):
            return False
        if cats and not set(cats) & set(a["categories"]):
            return False
        if writers and a["writer"] not in writers:
            return False
        if dfrom and a["posted_on"] < date.fromisoformat(dfrom):
            return False
        if dto and a["posted_on"] > date.fromisoformat(dto):
            return False
        if pinned == "only" and not a["is_pinned"]:
            return False
        if pinned == "exclude" and a["is_pinned"]:
            return False
        has = a["deadline_at"] is not None
        if has_dl is True and not has:
            return False
        if has_dl is False and has:
            return False
        if ranged:
            if not has:
                return False
            d = _day(a["deadline_at"])
            if xfrom and d < date.fromisoformat(xfrom):
                return False
            if xto and d > date.fromisoformat(xto):
                return False
        if sort == "deadline_asc":
            if not has:
                return False
            if not ranged and _day(a["deadline_at"]) < _day(now):
                return False
        return True

    items = [a for a in rows if keep(a)]
    if sort == "posted_asc":
        items.sort(key=lambda a: (a["posted_on"], a["id"]))
    elif sort == "views_desc":
        items.sort(key=lambda a: (-(a["view_count"] or 0), -a["posted_on"].toordinal(), -a["id"]))
    elif sort == "deadline_asc":
        items.sort(key=lambda a: (a["deadline_at"], a["id"]))
    else:
        items.sort(key=lambda a: (-a["posted_on"].toordinal(), -a["id"]))

    page = p.get("page", 1)
    size = min(50, max(1, p.get("size", 20)))
    start = (page - 1) * size
    return {
        "article_nos": [a["article_no"] for a in items[start : start + size]],
        "total": len(items),
        "page": page,
        "size": size,
        "has_next": start + size < len(items),
    }
