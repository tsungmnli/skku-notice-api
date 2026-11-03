import httpx2

from app.auth.turnstile import VERIFY_URL, verify_turnstile
from tests.conftest import make_settings


def client_returning(payload=None, status=200, seen=None):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append((str(request.url), request.content.decode()))
        return httpx2.Response(status, json=payload or {})

    return httpx2.Client(transport=httpx2.MockTransport(handler))


S = make_settings(turnstile_secret_key="secret")


def test_skipped_without_a_secret_outside_prod():
    assert verify_turnstile(make_settings(), None) is True


def test_a_missing_secret_in_prod_fails(monkeypatch):
    s = make_settings()
    monkeypatch.setattr(s, "app_env", "prod")
    assert verify_turnstile(s, "tok") is False


def test_missing_token_fails_without_calling_cloudflare():
    seen = []
    assert verify_turnstile(S, "", client=client_returning(seen=seen)) is False
    assert seen == []


def test_success_sends_secret_token_and_ip():
    seen = []
    ok = verify_turnstile(
        S, "tok", "198.51.100.7", client=client_returning({"success": True}, seen=seen)
    )
    assert ok is True
    url, body = seen[0]
    assert url == VERIFY_URL
    assert "secret=secret" in body and "response=tok" in body and "remoteip=198.51.100.7" in body


def test_rejected_token_fails():
    assert verify_turnstile(S, "tok", client=client_returning({"success": False})) is False


def test_cloudflare_errors_fail_closed():
    assert verify_turnstile(S, "tok", client=client_returning(status=500)) is False

    def broken(request):
        raise httpx2.ConnectError("down")

    assert (
        verify_turnstile(S, "tok", client=httpx2.Client(transport=httpx2.MockTransport(broken)))
        is False
    )
