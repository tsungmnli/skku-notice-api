"""Parse the board's list page. Pure functions: HTML in, dataclasses out (no network, no DB)."""

import re
from dataclasses import dataclass
from datetime import date

from bs4 import BeautifulSoup, Tag

from app.crawler.categories import category_code, normalize_label

BOARD_URL = "https://www.skku.edu/skku/campus/skk_comm/notice01.do"

_ARTICLE_NO_RE = re.compile(r"articleNo=(\d+)")
_DISPLAY_NO_RE = re.compile(r"No\.\s*(\d+)")
_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_INT_RE = re.compile(r"\d[\d,]*")


class ParseError(ValueError):
    """The page no longer looks like the board we know (markup changed, error page, ...)."""


@dataclass(frozen=True)
class ListedArticle:
    article_no: int
    title: str
    category_label: str | None  # as the board writes it, without brackets; None if absent
    category_code: str
    writer: str
    posted_on: date
    display_no: int | None  # `No.26157`; pinned posts show "공지" instead -> None
    is_pinned: bool
    view_count: int | None
    has_attachment: bool
    url: str


def article_url(article_no: int) -> str:
    return f"{BOARD_URL}?mode=view&articleNo={article_no}"


def list_page_url(offset: int, limit: int = 10) -> str:
    return f"{BOARD_URL}?mode=list&articleLimit={limit}&article.offset={offset}"


def clean_text(value: str) -> str:
    """Collapse whitespace and drop the BOM / zero-width characters the board sometimes leaves."""
    value = value.replace("\ufeff", "").replace("\u200b", "").replace("\xa0", " ")
    return " ".join(value.split())


def _parse_row(li: Tag) -> ListedArticle:
    dt = li.select_one("dt.board-list-content-title")
    link = dt.select_one("a[href]") if dt else None
    if dt is None or link is None:
        raise ParseError("row without a title link")
    m = _ARTICLE_NO_RE.search(link["href"])
    if not m:
        raise ParseError(f"no articleNo in {link['href']!r}")

    label_el = dt.select_one(".c-board-list-category")
    raw_label = clean_text(label_el.get_text()) if label_el else ""
    label = normalize_label(raw_label) or None

    info = [clean_text(x.get_text(" ")) for x in li.select("dd.board-list-content-info li")]
    if len(info) < 3:
        raise ParseError(f"article {m.group(1)}: expected 4 info columns, got {info!r}")
    dm = _DATE_RE.search(info[2])
    if not dm:
        raise ParseError(f"article {m.group(1)}: bad date {info[2]!r}")
    no_m = _DISPLAY_NO_RE.search(info[0])
    views = _INT_RE.search(info[3]) if len(info) > 3 else None

    return ListedArticle(
        article_no=int(m.group(1)),
        title=clean_text(link.get_text(" ")),
        category_label=label,
        category_code=category_code(label),
        writer=info[1],
        posted_on=date(*map(int, dm.groups())),
        display_no=int(no_m.group(1)) if no_m else None,
        is_pinned="board-list-content-top" in (dt.get("class") or []),
        view_count=int(views.group().replace(",", "")) if views else None,
        has_attachment=li.select_one(".board-list-file") is not None,
        url=article_url(int(m.group(1))),
    )


def parse_list(html: str) -> list[ListedArticle]:
    """Every article row on one list page, pinned posts first (as on the page).

    An empty list is a valid result only for an offset past the last page; a page with no
    `ul.board-list-wrap` at all raises ParseError so a changed layout fails loudly.
    """
    soup = BeautifulSoup(html, "html.parser")
    wrap = soup.select_one("ul.board-list-wrap")
    if wrap is None:
        raise ParseError("ul.board-list-wrap not found")
    return [_parse_row(li) for li in wrap.find_all("li", recursive=False) if li.select_one("dl")]
