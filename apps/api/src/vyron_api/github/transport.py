"""Standard-library HTTP transport for the GitHub REST API (``urllib`` on a worker thread).

Security properties: the base URL is fixed by server configuration and is only ever
``https://api.github.com`` in production; request paths must be absolute API paths (never full URLs
from users); redirects are never followed (a redirect could leave the API host with the token
attached); the token is only sent in the ``Authorization`` header and is hidden from ``repr``;
responses are size-capped. Proxy and CA settings come from the standard environment variables.
"""

from __future__ import annotations

import asyncio
import http.client
import re
import ssl
import urllib.error
import urllib.request
from collections.abc import Mapping
from urllib.parse import urlsplit

from vyron_api.github.retry import RawResponse
from vyron_api.providers.github import GitHubError

DEFAULT_BASE_URL = "https://api.github.com"
MAX_BODY_BYTES = 5 * 1024 * 1024
_PATH_RE = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/?-]*$")
_LOCAL_TEST_HOSTS = frozenset({"127.0.0.1", "localhost"})
# GitHub tokens are printable ASCII with no spaces. Anything else (a pasted newline, a stray quote-and-space) is
# refused up front, so a bad value fails with a clear message instead of becoming a malformed header.
_TOKEN_RE = re.compile(r"^[\x21-\x7e]{1,255}$")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:  # type: ignore[override]
        return None  # surface 3xx as a response instead of following it


class UrllibGitHubTransport:
    def __init__(
        self,
        *,
        token: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        user_agent: str = "vyron-api/0.1",
        timeout: float = 15.0,
        api_version: str | None = "2022-11-28",  # GitHub's documented default; pinned explicitly
        allow_local_test_server: bool = False,
    ) -> None:
        parts = urlsplit(base_url)
        local_ok = allow_local_test_server and parts.hostname in _LOCAL_TEST_HOSTS
        if not local_ok and (parts.scheme != "https" or parts.hostname != "api.github.com"):
            raise ValueError("base_url must be https://api.github.com")
        if parts.username or parts.password or parts.path not in ("", "/") or parts.query:
            raise ValueError("base_url must not contain credentials, a path or a query")
        if not user_agent.strip():
            raise ValueError("GitHub requires a User-Agent header")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if token and not _TOKEN_RE.fullmatch(token):
            raise ValueError("the GitHub token must be 1-255 printable ASCII characters with no spaces")  # not echoed
        self._base = f"{parts.scheme}://{parts.netloc}"
        self._token = token or None
        self._timeout = timeout
        self._headers = {
            "accept": "application/vnd.github+json",
            "user-agent": user_agent,
        }
        if api_version:
            self._headers["x-github-api-version"] = api_version
        # The default context verifies certificates and host names and honours the standard CA environment
        # variables. It is passed explicitly so verification does not depend on urllib's defaults.
        self._context = ssl.create_default_context()
        self._opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=self._context))

    def __repr__(self) -> str:
        return f"UrllibGitHubTransport(base={self._base!r}, authenticated={self._token is not None})"

    @property
    def authenticated(self) -> bool:
        return self._token is not None

    def url_for(self, path_or_url: str) -> str:
        """Accept an API path, or an absolute URL that is already on the configured host."""
        if path_or_url.startswith(("https://", "http://")):
            if not path_or_url.startswith(self._base + "/"):
                raise ValueError("URL is not on the configured GitHub API host")
            return path_or_url
        path_only = path_or_url.split("?", 1)[0]
        if (
            not _PATH_RE.fullmatch(path_or_url)
            or "//" in path_or_url
            or "/../" in path_or_url
            or path_only.endswith("/..")
        ):
            raise ValueError("path must be an absolute GitHub API path")
        return self._base + path_or_url

    async def get(
        self, path_or_url: str, extra_headers: Mapping[str, str] | None = None
    ) -> RawResponse:
        url = self.url_for(path_or_url)
        headers = dict(self._headers)
        for name, value in (extra_headers or {}).items():
            headers[name.lower()] = value
        if self._token:
            headers["authorization"] = f"Bearer {self._token}"
        return await asyncio.to_thread(self._get_blocking, url, headers)

    def _get_blocking(self, url: str, headers: dict[str, str]) -> RawResponse:
        request = urllib.request.Request(url, headers=headers, method="GET")  # noqa: S310
        try:
            with self._opener.open(request, timeout=self._timeout) as response:  # noqa: S310
                return self._read(response.status, response.headers, response)
        except urllib.error.HTTPError as error:
            with error:
                return self._read(error.code, error.headers, error)
        except http.client.HTTPException as exc:
            # A response cut off mid-body (IncompleteRead) or malformed on the wire is a network failure, so
            # the retry policy treats it like one instead of letting it surface as an unhandled error.
            raise ConnectionError(f"GitHub response was not received intact ({type(exc).__name__})") from exc

    @staticmethod
    def _read(status: int, headers: object, stream: object) -> RawResponse:
        body = stream.read(MAX_BODY_BYTES + 1)  # type: ignore[attr-defined]
        if len(body) > MAX_BODY_BYTES:
            raise GitHubError("GitHub response is larger than the allowed size")
        lowered = {k.lower(): v for k, v in headers.items()}  # type: ignore[attr-defined]
        # http.client hands back a short body without complaint when the connection drops mid-response. Kept as
        # a response it would be cached under its ETag and then served on every 304, so a short body is a network
        # failure instead (the retry layer retries it). Responses that have no body, whatever they declare (a
        # 304 states the length of the body it stands in for), are not checked.
        declared = lowered.get("content-length", "")
        carries_body = status >= 200 and status not in (204, 304)
        if carries_body and declared.isdigit() and len(body) != int(declared):
            raise ConnectionError("GitHub response ended before its declared length")
        return RawResponse(status, lowered, body)
