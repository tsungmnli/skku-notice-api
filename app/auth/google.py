"""Google sign-in: the OAuth client and the rule for matching a Google identity to an account."""

from datetime import datetime

from authlib.integrations.starlette_client import OAuth
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.errors import AuthError
from app.auth.sessions import revoke_user_sessions
from app.config import Settings
from app.models import User

DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"


def google_enabled(settings: Settings) -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def build_oauth(settings: Settings) -> OAuth | None:
    """The Authlib registry for this app, or None if Google sign-in is not configured."""
    if not google_enabled(settings):
        return None
    oauth = OAuth()
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        server_metadata_url=DISCOVERY_URL,
        # PKCE on top of the state/nonce checks Authlib already does.
        client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"},
    )
    return oauth


def google_conflict() -> AuthError:
    return AuthError(
        "이 이메일은 이미 다른 Google 계정과 연결되어 있습니다.", "google_conflict", 409
    )


def upsert_google_user(
    db: Session, *, sub: str, email: str, name: str | None, now: datetime
) -> User:
    """The account for a Google identity whose e-mail Google reports as verified.

    1. Known `sub` -> that account (the address can change on Google's side; `sub` never does).
    2. Same e-mail already registered -> link it. If that account was never verified, whoever
       registered it did not prove they own the address (it may be a squatter), so its password
       and sessions are discarded; a verified account keeps everything.
    3. Otherwise create a verified, password-less account.

    Shared by the web callback and, later, the native-app token endpoint. The caller commits.
    """
    email = email.strip().lower()
    name = (name or "").strip()[:100] or None

    user = db.scalar(select(User).where(User.google_sub == sub))
    if user is not None:
        return user

    user = db.scalar(select(User).where(User.email == email))
    if user is not None:
        if user.google_sub is not None:  # and it is not this `sub`, or step 1 would have hit
            raise google_conflict()
        if user.email_verified_at is None:
            user.password_hash = None
            user.email_verified_at = now
            revoke_user_sessions(db, user.id)
        user.google_sub = sub
        user.name = user.name or name
        db.flush()
        return user

    user = User(email=email, google_sub=sub, name=name, email_verified_at=now)
    try:
        with db.begin_nested():
            db.add(user)
            db.flush()
    except IntegrityError:
        # A parallel callback created the same account first; use it.
        found = db.scalar(select(User).where(User.google_sub == sub))
        if found is None:
            raise google_conflict() from None
        return found
    return user
