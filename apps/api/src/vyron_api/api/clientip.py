"""Which client address to rate-limit.

``X-Forwarded-For`` is attacker-controlled unless a trusted reverse proxy wrote its last entry, so it
is ignored by default. With ``trusted_hops`` proxies in front, the address that the outermost trusted
proxy saw is the ``trusted_hops``-th entry from the right; anything to its left is client-supplied and
is never used. Fewer entries than trusted hops means the header cannot be trusted, so the direct peer
address is used instead.
"""

from __future__ import annotations

from collections.abc import Mapping

MAX_ADDRESS_LENGTH = 64


def select_client_ip(direct: str | None, headers: Mapping[str, str], trusted_hops: int) -> str:
    peer = direct or "unknown"
    if trusted_hops < 1:
        return peer
    hops = [part.strip() for part in headers.get("x-forwarded-for", "").split(",") if part.strip()]
    if len(hops) < trusted_hops:
        return peer
    return hops[-trusted_hops][:MAX_ADDRESS_LENGTH]
