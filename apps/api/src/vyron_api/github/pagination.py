"""Link-header pagination for the GitHub REST API.

GitHub's docs say to follow the URLs in the ``Link`` header instead of building page URLs by hand,
and the set of rel values present varies by position (``next``/``prev``/``first``/``last``). A
followed URL comes from a response, so it is untrusted: it is only accepted if it stays on the
expected API host over https.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import urlsplit

_MAX_HEADER_LENGTH = 8192
_LINK_RE = re.compile(
    r"<(?P<url>[^<>]*)>"
    r"(?P<params>(?:\s*;\s*[^;,=\s]+(?:\s*=\s*(?:\"[^\"]*\"|[^;,]*))?)*)"
)
_REL_RE = re.compile(r"""rel\s*=\s*(?:"(?P<quoted>[^"]*)"|(?P<bare>[^\s;,]+))""", re.IGNORECASE)


class UnsafePaginationUrlError(ValueError):
    """A pagination URL points somewhere other than the expected API host."""


def parse_link_header(value: str | None) -> dict[str, str]:
    """Return ``{rel: url}``. Malformed entries are skipped; the first URL per rel wins."""
    if not value or len(value) > _MAX_HEADER_LENGTH:
        return {}
    links: dict[str, str] = {}
    for match in _LINK_RE.finditer(value):
        rel_match = _REL_RE.search(match.group("params") or "")
        if not rel_match:
            continue
        rels = (rel_match.group("quoted") or rel_match.group("bare") or "").split()
        for rel in rels:
            links.setdefault(rel.lower(), match.group("url").strip())
    return links


def validate_pagination_url(url: str, *, allowed_host: str = "api.github.com") -> str:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise UnsafePaginationUrlError("pagination URL is malformed") from exc
    if parts.scheme != "https" or parts.hostname != allowed_host:
        raise UnsafePaginationUrlError("pagination URL leaves the API host")
    if parts.username is not None or parts.password is not None or port is not None:
        raise UnsafePaginationUrlError("pagination URL has credentials or a port")
    return url


def next_page_url(headers: Mapping[str, str], *, allowed_host: str = "api.github.com") -> str | None:
    """The validated ``rel="next"`` URL, or None on the last page. Raises if it is unsafe."""
    lowered = {key.lower(): value for key, value in headers.items()}
    url = parse_link_header(lowered.get("link")).get("next")
    return validate_pagination_url(url, allowed_host=allowed_host) if url else None
