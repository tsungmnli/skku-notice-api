"""Argon2id password hashing.

Hashing is deliberately expensive (64 MiB per hash by default). Routes are sync and run on a
threadpool of up to 40 threads, so without a cap 40 simultaneous logins would ask for ~2.5 GB
and OOM a small pod. A semaphore keeps the concurrent hashes to `MAX_CONCURRENT_HASHES`.
"""

import threading
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_LENGTH = 8
# Argon2 would hash megabytes just as happily; refuse them so a request body cannot be used to
# burn CPU.
MAX_LENGTH = 128
MAX_CONCURRENT_HASHES = 4

# argon2-cffi defaults (RFC 9106 low-memory profile): Argon2id, t=3, m=64 MiB, p=4.
_hasher = PasswordHasher()
_slots = threading.BoundedSemaphore(MAX_CONCURRENT_HASHES)


def hash_password(password: str) -> str:
    with _slots:
        return _hasher.hash(password)


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return hash_password("dummy-password-for-timing")


def verify_password(stored_hash: str | None, password: str) -> bool:
    """True if `password` matches. Never raises for a bad or missing hash.

    With no stored hash (unknown e-mail, or a Google-only account) a throwaway hash is
    verified instead, so the response takes as long as for a real account and does not reveal
    whether the address is registered.
    """
    target = stored_hash or _dummy_hash()
    with _slots:
        try:
            ok = _hasher.verify(target, password)
        except (VerificationError, InvalidHashError):
            return False
    return ok and stored_hash is not None


def needs_rehash(stored_hash: str) -> bool:
    """True if the hash was made with weaker parameters than the current ones.

    Call after a successful login (when the plaintext is at hand) and store a new hash if so.
    """
    return _hasher.check_needs_rehash(stored_hash)
