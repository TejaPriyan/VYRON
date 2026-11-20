"""One careful way to read JSON from a public package registry (standard library only).

What it does and does not do:

* HTTPS to the one host the caller names, nothing else. A redirect is refused rather than followed, so a
  registry (or something answering for it) cannot send this process to another host.
* The body is capped; a longer one is an error, not a truncated parse.
* Errors are the fixed messages in ``errors.py``, never the registry's text.
* No credentials are sent. A User-Agent says what the program is and gives no contact address, because VYRON
  does not have one to give.

It has not been run against a registry by any test: tests inject a transport. The live runs of
``scripts/ingest_package.py`` that were reported are the only observations of the real registries.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from vyron_api.registries.errors import PackageNotFoundError, RegistryError, RegistryRateLimitedError

MAX_RESPONSE_BYTES = 16 * 1024 * 1024  # PyPI's JSON for a package with many releases can reach several MiB
USER_AGENT = "vyron-registry-reader/0.1 (local development)"
LOCAL_TEST_HOSTS = frozenset({"127.0.0.1", "localhost"})


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None  # urllib then raises the 3xx as an HTTPError, which fetch_json reports


_OPENER = urllib.request.build_opener(_RefuseRedirects)


def fetch_json(
    url: str,
    *,
    host: str,
    what: str,
    timeout: float = 10.0,
    transport: Any | None = None,
    allow_local_test_server: bool = False,
) -> Any:
    """GET ``url`` (HTTPS, exactly ``host``) and return the parsed JSON. ``what`` names the registry in errors.

    ``allow_local_test_server`` also accepts plain HTTP to a loopback address, for a stand-in that is run instead
    of the registry. It is never set from configuration read from the environment.
    """
    parts = urlsplit(url)
    real = parts.scheme == "https" and parts.hostname == host
    local = allow_local_test_server and parts.scheme == "http" and parts.hostname in LOCAL_TEST_HOSTS
    if not (real or local) or parts.username or parts.password:
        raise RegistryError(f"refusing to read {what} from an unexpected address")
    # The address was checked above: https to the one registry host, or loopback http for a local stand-in.
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    request = urllib.request.Request(url, headers=headers)  # noqa: S310
    opener = transport if transport is not None else _OPENER
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code == 404:
            raise PackageNotFoundError(f"{what} has no such package") from None
        if exc.code == 429:
            raise RegistryRateLimitedError(f"{what} is limiting requests from this address; try again later") from None
        if 300 <= exc.code < 400:
            raise RegistryError(f"{what} answered with a redirect, which is not followed") from None
        raise RegistryError(f"{what} answered HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError):  # includes timeouts and refused connections
        raise RegistryError(f"could not reach {what}") from None
    if len(body) > MAX_RESPONSE_BYTES:
        raise RegistryError(f"{what} sent more than {MAX_RESPONSE_BYTES // (1024 * 1024)} MiB")
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise RegistryError(f"{what} did not send valid JSON") from None


def text_field(container: Any, key: str, *, max_chars: int = 300) -> str:
    """A string field, stripped and capped; anything else is an empty string."""
    value = container.get(key) if isinstance(container, dict) else None
    return value.strip()[:max_chars] if isinstance(value, str) else ""
