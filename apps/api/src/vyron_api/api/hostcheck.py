"""Which ``Host`` header values a server answers to.

A development server bound to 127.0.0.1 is reachable from any web page the person visits, because a hostile
site can point its own DNS name at 127.0.0.1 ("DNS rebinding") and then call the server as if it were
same-origin. Browsers send the attacker's name in ``Host``, so refusing every name but the loopback ones closes
that off. It matters most when the server holds a GitHub token, since the token would otherwise be spendable by
that page. This is a guard for the development server; a deployed service sits behind a proxy that does its own
host routing and passes its own allowed list (or none).
"""

from __future__ import annotations

from collections.abc import Collection

LOOPBACK_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})

_MAX_HEADER_CHARS = 255


def hostname_of(header: str | None) -> str | None:
    """The host part of a ``Host`` header, lower-cased and without its port, or None if it is malformed.

    ``localhost:8000`` -> ``localhost``;  ``[::1]:8000`` -> ``::1``. Anything that is not a plain host with an
    optional numeric port (user info, a second colon, a trailing path) is malformed rather than guessed at.
    """
    if not header or len(header) > _MAX_HEADER_CHARS:
        return None
    value = header.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        if end == -1:
            return None
        host, rest = value[1:end], value[end + 1 :]
        if rest and not (rest.startswith(":") and rest[1:].isdigit()):
            return None
        return host or None
    host, separator, port = value.partition(":")
    if separator and not port.isdigit():
        return None
    return host or None


def host_allowed(header: str | None, allowed: Collection[str]) -> bool:
    host = hostname_of(header)
    return host is not None and host in {name.lower() for name in allowed}


def is_loopback_bind(host: str) -> bool:
    """True when a bind address only accepts connections from this machine."""
    return host.lower() in LOOPBACK_HOSTS
