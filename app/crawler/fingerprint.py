"""Fingerprint of a notice's view page: did the *post* change, or only the page around it?

Not wired into the crawler yet. `scripts/audit_body_hash.py` uses it to measure how often a hash of
the body would say "edited" for a post nobody touched (a counter, a date stamp, a rotating banner
inside the box). Only when that rate is near zero does it make sense to store a hash and let it
drive UPDATED (README, "본문 변경 감지").

Pure functions: HTML in, hashes out. The text is only compared, never stored by the crawler.
"""

import hashlib
from dataclasses import dataclass

from bs4 import BeautifulSoup

from app.crawler.parser import ParseError, parse_detail


@dataclass(frozen=True)
class Fingerprint:
    lines: tuple[str, ...]  # the body, one normalized non-empty line per entry
    text_hash: str  # of `lines`: what `parse_detail` calls the body
    box_hash: str  # of the whole write box: the body plus whatever else sits in it (e.g. files)


def normalize_lines(text: str) -> tuple[str, ...]:
    """Whitespace-insensitive lines: trailing spaces, tab/space runs, no-break spaces and blank
    lines are layout, not content, so re-flowing a post must not look like an edit."""
    text = text.replace("﻿", "").replace("​", "").replace("\xa0", " ")
    return tuple(line for line in (" ".join(raw.split()) for raw in text.splitlines()) if line)


def digest(lines: tuple[str, ...]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16]


def fingerprint(html: str) -> Fingerprint:
    """Raises ParseError exactly when `parse_detail` would (no write box: error page, deleted)."""
    body = normalize_lines(parse_detail(html))
    box = BeautifulSoup(html, "html.parser").select_one("dl.board-write-box")
    if box is None:  # parse_detail found it a moment ago; kept as a guard against refactors
        raise ParseError("dl.board-write-box not found")
    for noise in box.find_all(("script", "style")):
        noise.decompose()
    box_lines = normalize_lines(box.get_text("\n"))
    return Fingerprint(body, digest(body), digest(box_lines))


def changed_lines(old: tuple[str, ...], new: tuple[str, ...], *, limit: int = 6) -> list[str]:
    """A few lines that differ, `- old` / `+ new`, to see *what* moved. Order-insensitive on
    purpose: for an audit it is enough to know which lines appeared or went away."""
    old_set, new_set = set(old), set(new)
    gone = [line for line in old if line not in new_set]
    came = [line for line in new if line not in old_set]
    out = [f"- {line[:100]}" for line in gone[:limit]]
    out += [f"+ {line[:100]}" for line in came[:limit]]
    hidden = max(0, len(gone) - limit) + max(0, len(came) - limit)
    if hidden:
        out.append(f"… and {hidden} more changed lines")
    return out
