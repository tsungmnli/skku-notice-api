"""Server-side check of a Cloudflare Turnstile token (the sign-up form's bot check)."""

import logging

import httpx2

from app.config import Settings

logger = logging.getLogger(__name__)

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def verify_turnstile(
    settings: Settings,
    token: str | None,
    remote_ip: str | None = None,
    *,
    client: httpx2.Client | None = None,
) -> bool:
    """True if Cloudflare confirms the token.

    Without a configured secret the check is skipped in dev/test and fails in prod (which the
    prod guard makes unreachable). If Cloudflare cannot be reached it fails closed.
    """
    if not settings.turnstile_secret_key:
        return settings.app_env != "prod"
    if not token:
        return False

    data = {"secret": settings.turnstile_secret_key, "response": token}
    if remote_ip and remote_ip != "unknown":
        data["remoteip"] = remote_ip
    owns_client = client is None
    client = client or httpx2.Client(timeout=httpx2.Timeout(5.0))
    try:
        resp = client.post(VERIFY_URL, data=data)
        resp.raise_for_status()
        return resp.json().get("success") is True
    except (httpx2.HTTPError, ValueError) as exc:
        logger.warning("turnstile verification unavailable: %s", exc)
        return False
    finally:
        if owns_client:
            client.close()
