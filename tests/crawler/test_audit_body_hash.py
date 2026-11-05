import json
from datetime import date
from pathlib import Path

import pytest

from app.crawler.fetch import FetchError
from app.crawler.fingerprint import fingerprint
from scripts import audit_body_hash as audit
from scripts.audit_deadlines import Notice

FIXTURES = Path(__file__).parent / "fixtures"
PAGE = (FIXTURES / "detail_141051.html").read_text(encoding="utf-8")
EDITED = PAGE.replace("e-mail로 제출", "방문 제출")
NOTICE = Notice(141051, "공모전", date(2026, 11, 2))


class Source:
    """Answers with the given pages in turn; an Exception in the list is raised."""

    def __init__(self, *answers):
        self.answers = list(answers)

    def detail_page(self, article_no: int) -> str:
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_two_identical_readings_are_not_noisy():
    reading = audit.read_one(Source(PAGE, PAGE), NOTICE, twice=True)
    assert not reading.noisy_text and not reading.noisy_box and reading.error is None


def test_a_body_that_moves_between_two_readings_is_noise():
    reading = audit.read_one(Source(PAGE, EDITED), NOTICE, twice=True)
    assert reading.noisy_text and not reading.noisy_box
    assert "본문" in audit.render_twice([reading])


def test_noise_outside_the_body_is_reported_as_box_noise():
    noisy = PAGE.replace("<dd>", "<dd><span>조회수 12</span>", 1)
    reading = audit.read_one(Source(PAGE, noisy), NOTICE, twice=True)
    assert reading.noisy_box and not reading.noisy_text


def test_a_failed_page_is_recorded_not_raised():
    reading = audit.read_one(Source(FetchError("down")), NOTICE, twice=False)
    assert reading.error == "down" and reading.first is None
    assert "down" in audit.render_twice([reading])


def test_classify_compares_with_the_snapshot():
    fp = fingerprint(PAGE)
    entry = audit.entry_of(NOTICE, fp)
    assert audit.classify(None, fp) == audit.NEW
    assert audit.classify(entry, fp) == audit.SAME
    assert audit.classify(entry, fingerprint(EDITED)) == audit.TEXT_CHANGED
    noisy = fingerprint(PAGE.replace("<dd>", "<dd><span>조회수 12</span>", 1))
    assert audit.classify(entry, noisy) == audit.BOX_ONLY


def test_snapshot_round_trip_and_report(tmp_path: Path):
    path = tmp_path / "body.json"
    assert audit.load_snapshot(path) == {}  # a missing file is an empty snapshot
    first = audit.read_one(Source(PAGE), NOTICE, twice=False)
    audit.save_snapshot(path, {str(NOTICE.article_no): audit.entry_of(NOTICE, first.first)})
    assert json.loads(path.read_text("utf-8"))["items"]["141051"]["text_hash"]

    later = audit.read_one(Source(EDITED), NOTICE, twice=False)
    text, counts = audit.render_snapshot([later], audit.load_snapshot(path))
    assert counts[audit.TEXT_CHANGED] == 1 and counts[audit.SAME] == 0
    assert "- " in text and "+ " in text  # shows which lines moved

    same, counts = audit.render_snapshot([first], audit.load_snapshot(path))
    assert same == "" and counts[audit.SAME] == 1


def test_summary_counts_noisy_posts():
    clean = audit.read_one(Source(PAGE, PAGE), NOTICE, twice=True)
    noisy = audit.read_one(Source(PAGE, EDITED), NOTICE, twice=True)
    summary = audit.summarize([clean, noisy], None, twice=True)
    assert "읽음 2건" in summary and "본문 1건" in summary


def test_the_cli_needs_a_mode():
    with pytest.raises(SystemExit):
        audit.main(["--article-no", "1"])
