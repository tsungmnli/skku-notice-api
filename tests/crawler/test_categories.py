from app.crawler.categories import CATEGORIES, LABEL_TO_CODE
from scripts.seed_dev import CATEGORIES as SEED_CATEGORIES


def test_matches_seed_and_covers_every_label():
    assert CATEGORIES == SEED_CATEGORIES
    assert set(LABEL_TO_CODE.values()) == {code for code, _ in CATEGORIES}
