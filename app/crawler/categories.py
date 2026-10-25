"""The seven categories and how the board's own `[라벨]` maps onto them."""

import logging

logger = logging.getLogger(__name__)

# (code, display name). The order is the filter order in the UI (`categories.sort_order`).
# Must stay equal to the web app's CATEGORY_NAMES and scripts/seed_dev.py (a test checks it).
CATEGORIES: list[tuple[str, str]] = [
    ("academic", "학사"),
    ("admission", "입학"),
    ("career", "취업"),
    ("recruit", "채용·모집"),
    ("scholarship", "장학"),
    ("event", "행사·세미나"),
    ("general", "일반"),
]

# The board's `<select id="category1">` options, exactly as the school spells them.
LABEL_TO_CODE: dict[str, str] = {
    "학사": "academic",
    "입학": "admission",
    "취업": "career",
    "채용/모집": "recruit",
    "장학": "scholarship",
    "행사/세미나": "event",
    "일반": "general",
}

FALLBACK_CODE = "general"


def normalize_label(raw: str) -> str:
    """`[행사/세미나]` -> `행사/세미나`."""
    return raw.strip().strip("[]").strip()


def category_code(label: str | None) -> str:
    """Board label -> our category code. Unknown or missing labels fall back to `general`.

    A new label on the school's side must not stop the crawl, so it only logs a warning.
    """
    name = normalize_label(label or "")
    code = LABEL_TO_CODE.get(name)
    if code is None:
        logger.warning("unknown board category label %r; using %r", label, FALLBACK_CODE)
        return FALLBACK_CODE
    return code
