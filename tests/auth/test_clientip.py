from starlette.requests import Request

from app.auth.clientip import client_ip


def req(peer="10.0.0.9", xff=None):
    headers = [
        (b"x-forwarded-for", v.encode()) for v in ([xff] if isinstance(xff, str) else xff or [])
    ]
    scope = {"type": "http", "headers": headers, "client": (peer, 1234) if peer else None}
    return Request(scope)


def test_no_proxy_ignores_the_header_completely():
    assert client_ip(req(peer="203.0.113.5", xff="6.6.6.6"), 0) == "203.0.113.5"


def test_one_proxy_takes_the_last_entry():
    assert client_ip(req(xff="198.51.100.7"), 1) == "198.51.100.7"


def test_forged_prefix_is_ignored():
    # The client claims 6.6.6.6; Cloudflare appended the real address, ingress appended CF.
    r = req(xff="6.6.6.6, 198.51.100.7, 172.70.0.1")
    assert client_ip(r, 2) == "198.51.100.7"


def test_multiple_header_lines_are_joined_in_order():
    r = req(xff=["6.6.6.6", "198.51.100.7, 172.70.0.1"])
    assert client_ip(r, 2) == "198.51.100.7"


def test_fewer_entries_than_hops_falls_back_to_the_peer():
    assert client_ip(req(peer="10.0.0.9", xff="198.51.100.7"), 2) == "10.0.0.9"
    assert client_ip(req(peer="10.0.0.9"), 1) == "10.0.0.9"


def test_junk_is_never_used_as_a_key():
    assert client_ip(req(xff="not-an-ip"), 1) == "unknown"
    assert client_ip(req(peer=None), 0) == "unknown"


def test_ipv6_is_bucketed_by_64():
    a = client_ip(req(xff="2001:db8:1:2:aaaa:bbbb:cccc:dddd"), 1)
    b = client_ip(req(xff="2001:db8:1:2::1"), 1)
    assert a == b == "2001:db8:1:2::/64"


def test_ipv4_mapped_ipv6_is_plain_ipv4():
    assert client_ip(req(xff="::ffff:198.51.100.7"), 1) == "198.51.100.7"
