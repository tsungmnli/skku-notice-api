import threading
import time
from types import SimpleNamespace

import pytest
from argon2 import PasswordHasher

from app.auth import passwords


@pytest.fixture(autouse=True)
def fast_hasher(monkeypatch):
    # Cheap parameters keep the suite fast; the production defaults are checked separately.
    monkeypatch.setattr(
        passwords, "_hasher", PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    )
    passwords._dummy_hash.cache_clear()
    yield
    passwords._dummy_hash.cache_clear()


def test_hash_and_verify():
    h = passwords.hash_password("correct horse")
    assert h.startswith("$argon2id$")
    assert passwords.verify_password(h, "correct horse") is True
    assert passwords.verify_password(h, "wrong") is False


def test_hashes_are_salted():
    assert passwords.hash_password("same") != passwords.hash_password("same")


def test_missing_hash_never_verifies_but_still_costs_a_hash(monkeypatch):
    calls = []
    real = passwords._hasher

    def verify(h, p):
        calls.append(h)
        return real.verify(h, p)

    monkeypatch.setattr(passwords, "_hasher", SimpleNamespace(verify=verify, hash=real.hash))
    assert passwords.verify_password(None, "anything") is False
    assert passwords.verify_password(None, "dummy-password-for-timing") is False
    assert len(calls) == 2  # verification really ran against the dummy hash


def test_garbage_hash_is_a_failed_login_not_a_crash():
    assert passwords.verify_password("not-a-hash", "x") is False


def test_needs_rehash_detects_weaker_parameters(monkeypatch):
    old = passwords.hash_password("pw")
    assert passwords.needs_rehash(old) is False
    monkeypatch.setattr(
        passwords, "_hasher", PasswordHasher(time_cost=2, memory_cost=16, parallelism=1)
    )
    assert passwords.needs_rehash(old) is True


def test_production_defaults_are_argon2id_64mib():
    h = PasswordHasher()
    assert (h.time_cost, h.memory_cost, h.parallelism) == (3, 65536, 4)


def test_concurrent_hashes_are_capped(monkeypatch):
    running = peak = 0
    lock = threading.Lock()

    def slow_hash(_pw):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with lock:
            running -= 1
        return "h"

    monkeypatch.setattr(passwords, "_hasher", SimpleNamespace(hash=slow_hash))
    threads = [threading.Thread(target=passwords.hash_password, args=("x",)) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak == passwords.MAX_CONCURRENT_HASHES
