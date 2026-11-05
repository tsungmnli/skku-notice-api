"""The NEW / UPDATED display window (app/freshness.py).

The web computes the badges itself and keeps its own copy of the window length, so this pins
the number and the Seoul-midnight boundary: a server that disagreed would delete read marks the
web still needs. The badge rules are in docs/api-contract.md; the web owns their tests.
"""

from datetime import UTC, date, datetime, timedelta

from app.freshness import FRESH_DAYS, window_start
from app.timeutil import KST

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=KST)
START = date(2026, 10, 30)


def test_the_window_is_seven_seoul_days_today_included():
    assert FRESH_DAYS == 7
    assert window_start(NOW) == START
    # 15:00 UTC is midnight in Seoul: the day starts there, whatever zone `now` arrives in.
    assert window_start(datetime(2026, 11, 4, 15, 0, tzinfo=UTC)) == START
    assert window_start(datetime(2026, 11, 4, 14, 59, tzinfo=UTC)) == START - timedelta(days=1)
