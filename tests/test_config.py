import pytest
from pydantic import ValidationError

from app.config import Settings

PROD = {
    "app_env": "prod",
    "database_url": "postgresql://u:p@db/skku",
    "public_url": "https://skku.example.com",
    "cors_origins": "https://skku.example.com",
    "session_secret": "s" * 32,
    "smtp_host": "smtp.resend.com",
    "smtp_user": "resend",
    "smtp_password": "re_test",
    "turnstile_secret_key": "turnstile-test",
    "trusted_proxy_hops": 2,
}


def settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore


def test_dev_needs_nothing():
    s = settings()
    assert s.app_env == "dev"
    assert s.docs_on is True


def test_cors_origins_parsed_from_comma_separated_env(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test/, http://b.test")
    assert settings().cors_origins == ["http://a.test", "http://b.test"]


def test_prod_ok_and_docs_off_by_default():
    s = settings(**PROD)
    assert s.docs_on is False


def test_docs_can_be_forced_on_in_prod():
    assert settings(**PROD, docs_enabled=True).docs_on is True


@pytest.mark.parametrize(
    "missing",
    [
        "database_url",
        "public_url",
        "cors_origins",
        "session_secret",
        "smtp_host",
        "smtp_user",
        "smtp_password",
        "turnstile_secret_key",
        "trusted_proxy_hops",
    ],
)
def test_prod_refuses_to_boot_when_required_value_is_missing(missing):
    values = {k: v for k, v in PROD.items() if k != missing}
    with pytest.raises(ValidationError):
        settings(**values)


def test_prod_rejects_wildcard_cors():
    with pytest.raises(ValidationError, match="must not contain"):
        settings(**{**PROD, "cors_origins": "*"})


def test_dev_auth_defaults():
    s = settings()
    assert s.session_ttl_days == 30
    assert s.smtp_port == 587
    assert s.smtp_from == "SKKU 공지 <noreply@skkunoti.tsungmn.com>"
    assert s.trusted_proxy_hops is None


def test_auth_values_parsed_from_env(monkeypatch):
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "2")
    s = settings()
    assert s.smtp_port == 465
    assert s.trusted_proxy_hops == 2


def test_prod_rejects_short_session_secret():
    with pytest.raises(ValidationError, match="at least 32"):
        settings(**{**PROD, "session_secret": "short"})


def test_prod_accepts_zero_proxy_hops():
    assert settings(**{**PROD, "trusted_proxy_hops": 0}).trusted_proxy_hops == 0


def test_negative_proxy_hops_rejected():
    with pytest.raises(ValidationError):
        settings(trusted_proxy_hops=-1)


@pytest.mark.parametrize("only", [{"google_client_id": "id"}, {"google_client_secret": "secret"}])
def test_google_credentials_must_come_as_a_pair(only):
    with pytest.raises(ValidationError, match="together"):
        settings(**only)


def test_google_credentials_pair_is_accepted():
    s = settings(google_client_id="id", google_client_secret="secret", session_secret="s" * 32)
    assert s.google_client_id == "id"


def test_google_needs_a_session_secret_to_sign_its_state_cookie():
    with pytest.raises(ValidationError, match="SESSION_SECRET"):
        settings(google_client_id="id", google_client_secret="secret")
