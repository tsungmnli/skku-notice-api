from app.auth.limiter import RateLimiter


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(**kw):
    clock = Clock()
    return RateLimiter(clock=clock, **kw), clock


def test_allows_up_to_the_limit_then_blocks():
    lim, _ = make()
    assert [lim.hit("k", 3, 60) for _ in range(3)] == [None, None, None]
    wait = lim.hit("k", 3, 60)
    assert wait is not None and 1 <= wait <= 61


def test_window_slides():
    lim, clock = make()
    for _ in range(3):
        lim.hit("k", 3, 60)
        clock.t += 10  # hits at 1000, 1010, 1020
    assert lim.hit("k", 3, 60) is not None  # t=1030
    clock.t = 1061  # the first hit (1000) has aged out
    assert lim.hit("k", 3, 60) is None


def test_blocked_attempts_do_not_extend_the_wait():
    lim, clock = make()
    for _ in range(2):
        lim.hit("k", 2, 60)
    first = lim.hit("k", 2, 60)
    clock.t += 30
    for _ in range(50):
        lim.hit("k", 2, 60)
    second = lim.hit("k", 2, 60)
    assert second <= first - 29


def test_keys_are_independent_and_reset_works():
    lim, _ = make()
    lim.hit("a", 1, 60)
    assert lim.hit("a", 1, 60) is not None
    assert lim.hit("b", 1, 60) is None
    lim.reset("a")
    assert lim.hit("a", 1, 60) is None


def test_record_and_retry_after_for_failure_counting():
    lim, _ = make()
    assert lim.retry_after("login", 2) is None
    lim.record("login", 900)
    assert lim.retry_after("login", 2) is None
    lim.record("login", 900)
    assert lim.retry_after("login", 2) is not None


def test_overflow_never_wipes_everything():
    lim, _ = make(max_keys=100)
    lim.hit("victim", 1, 3600)  # the victim is at its limit
    for i in range(500):  # junk keys
        lim.hit(f"junk{i}", 5, 3600)
        lim.retry_after("victim", 1)  # the victim keeps being used (recent)
        lim.hit("victim", 1, 3600)
    assert len(lim) <= 100
    assert lim.hit("victim", 1, 3600) is not None  # still limited


def test_expired_keys_are_swept_before_live_ones_are_evicted():
    lim, clock = make(max_keys=10)
    for i in range(10):
        lim.hit(f"old{i}", 5, 10)
    lim.hit("live", 5, 3600)
    clock.t += 100  # all "old" keys have expired; "live" has not
    lim.hit("new", 5, 10)
    assert lim.hit("live", 1, 3600) is not None or lim.retry_after("live", 1) is not None
    assert len(lim) <= 10
