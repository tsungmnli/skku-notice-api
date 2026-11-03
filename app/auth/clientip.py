"""The real client address, without trusting what the client says about itself.

`X-Forwarded-For` is a list each proxy appends to, so everything the client sent sits on the
left and only the entries added by our own proxies (the right end) can be believed. With N
trusted proxies in front of the app, the address the outermost one saw is the Nth from the end.
"""

import ipaddress

from fastapi import Request

UNKNOWN = "unknown"


def client_ip(request: Request, trusted_hops: int) -> str:
    """`trusted_hops` is the number of proxies between the client and this app (0 = none, then
    the header is ignored entirely)."""
    peer = request.client.host if request.client else UNKNOWN
    if trusted_hops <= 0:
        return _normalize(peer)

    chain = [
        part.strip()
        for value in request.headers.getlist("x-forwarded-for")
        for part in value.split(",")
        if part.strip()
    ]
    if len(chain) < trusted_hops:
        # Fewer entries than proxies we were told about: the config is off, or the request
        # bypassed the proxy. Do not guess; the connecting address is the safe answer.
        return _normalize(peer)
    return _normalize(chain[-trusted_hops])


def _normalize(value: str) -> str:
    """Canonical text form; an IPv6 address is reduced to its /64, since one subscriber
    typically holds a whole /64 and could otherwise dodge limits by rotating addresses."""
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return UNKNOWN
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return str(addr.ipv4_mapped)
        return str(ipaddress.ip_network((addr, 64), strict=False).network_address) + "/64"
    return str(addr)
