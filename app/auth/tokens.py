"""Random secrets (session ids, e-mail links). Only their SHA-256 is ever stored."""

import hashlib
import secrets


def new_token() -> str:
    """256 bits of randomness, URL-safe (43 characters)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Hex SHA-256. A fast hash is right here: the input is already 256 random bits, so there
    is nothing to brute-force, and a database leak yields no usable tokens."""
    return hashlib.sha256(token.encode()).hexdigest()
