"""Rate-limit policy for the auth endpoints, in one place.

Each entry is (max events, window in seconds). The in-process limiter is a backstop; the
ingress / Cloudflare limit is the first line (see limiter.py).
"""

from app.auth.errors import rate_limited
from app.auth.limiter import RateLimiter

MINUTE = 60
HOUR = 3600

# Failed logins. Keyed by (email, ip) so a stranger cannot lock the real owner out, with a
# higher cap per email alone and a per-IP cap against one address trying many emails.
LOGIN_FAIL_EMAIL_IP = (10, 15 * MINUTE)
LOGIN_FAIL_EMAIL = (30, 15 * MINUTE)
LOGIN_FAIL_IP = (50, 15 * MINUTE)

# Anything that sends mail to an address. Counted whether or not the address is registered,
# so it neither leaks existence nor lets someone flood a victim's inbox.
MAIL_PER_EMAIL = (3, HOUR)
MAIL_PER_IP = (10, HOUR)
REGISTER_PER_IP = (5, HOUR)

VERIFY_TOKEN_PER_IP = (30, HOUR)
RESET_PER_IP = (30, HOUR)
DELETE_FAIL_PER_USER = (5, 15 * MINUTE)


def enforce(limiter: RateLimiter, key: str, policy: tuple[int, int]) -> None:
    """Count one event; 429 if the key is over its limit."""
    wait = limiter.hit(key, *policy)
    if wait is not None:
        raise rate_limited(wait)


def enforce_not_blocked(limiter: RateLimiter, key: str, policy: tuple[int, int]) -> None:
    """429 if the key is at its limit. Counts nothing (failures are recorded separately)."""
    wait = limiter.retry_after(key, policy[0])
    if wait is not None:
        raise rate_limited(wait)
