import pytest
from pydantic import ValidationError

from app.config import Settings

PROD = {
    "app_env": "prod",
    "database_url": "postgresql://u:p@db/skku",
    "public_url": "https://skku.example.com",
    "cors_origins": "https://skku.example.com",
}


def settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


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


@pytest.mark.parametrize("missing", ["database_url", "public_url", "cors_origins"])
def test_prod_refuses_to_boot_when_required_value_is_missing(missing):
    values = {k: v for k, v in PROD.items() if k != missing}
    with pytest.raises(ValidationError):
        settings(**values)


def test_prod_rejects_wildcard_cors():
    with pytest.raises(ValidationError, match="must not contain"):
        settings(**{**PROD, "cors_origins": "*"})
