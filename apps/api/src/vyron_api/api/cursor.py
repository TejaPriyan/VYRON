"""Signed, tamper-evident pagination cursors.

A cursor carries the next offset, an expiry, and a fingerprint of the operation and its filters, all
signed with HMAC-SHA256. Clients treat it as opaque. Signing means a client cannot jump to an
arbitrary offset, replay a cursor against a different query (the fingerprint would differ), or keep
using one forever. The first secret signs; every secret verifies, so keys can be rotated by adding a
new first key and dropping the old one later.

Offsets are only as stable as the data: if rows are inserted between pages a client may see a
duplicate or skip an item. That is a documented trade-off of offset paging; keyset cursors are a
later optimisation for very large collections.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from vyron_api.api.limits import MAX_CURSOR_LENGTH

MIN_SECRET_BYTES = 32
MAX_OFFSET = 1_000_000
DEFAULT_TTL_SECONDS = 24 * 3600
_VERSION = 1
_MAC_BYTES = 16


class InvalidCursorError(ValueError):
    """The cursor is malformed, forged, expired, or belongs to a different query."""


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as exc:
        raise InvalidCursorError("cursor is not valid base64url") from exc


def fingerprint(operation_id: str, filters: Mapping[str, Any]) -> str:
    """Stable short hash of the operation and its filter values (never of limit or cursor)."""
    canonical = json.dumps({"op": operation_id, "filters": filters}, sort_keys=True, separators=(",", ":"), default=str)
    return _b64e(hashlib.sha256(canonical.encode("utf-8")).digest()[:12])


class CursorCodec:
    def __init__(
        self,
        secrets: Sequence[bytes],
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        now: Callable[[], float] = time.time,
    ) -> None:
        if not secrets:
            raise ValueError("at least one cursor secret is required")
        if any(len(secret) < MIN_SECRET_BYTES for secret in secrets):
            raise ValueError(f"cursor secrets must be at least {MIN_SECRET_BYTES} bytes")
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be positive")
        self._secrets = tuple(secrets)
        self._ttl = ttl_seconds
        self._now = now

    def _mac(self, secret: bytes, body: str) -> bytes:
        return hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest()[:_MAC_BYTES]

    def encode(self, operation_id: str, filters: Mapping[str, Any], offset: int) -> str:
        if not 0 <= offset <= MAX_OFFSET:
            raise ValueError("offset out of range")
        payload = {
            "v": _VERSION,
            "o": offset,
            "e": int(self._now()) + self._ttl,
            "f": fingerprint(operation_id, filters),
        }
        body = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        return f"{body}.{_b64e(self._mac(self._secrets[0], body))}"

    def decode(self, token: str, operation_id: str, filters: Mapping[str, Any]) -> int:
        """The offset the cursor points at, or ``InvalidCursorError``."""
        if not token or len(token) > MAX_CURSOR_LENGTH:
            raise InvalidCursorError("cursor has an invalid length")
        body, separator, mac_text = token.partition(".")
        if not separator or not body or not mac_text or "." in mac_text:
            raise InvalidCursorError("cursor is malformed")
        try:
            body.encode("ascii")
        except UnicodeEncodeError as exc:
            raise InvalidCursorError("cursor is malformed") from exc
        supplied = _b64d(mac_text)
        if not any(hmac.compare_digest(supplied, self._mac(secret, body)) for secret in self._secrets):
            raise InvalidCursorError("cursor signature is invalid")
        try:
            payload = json.loads(_b64d(body))
        except (ValueError, UnicodeDecodeError) as exc:
            raise InvalidCursorError("cursor payload is malformed") from exc
        if not isinstance(payload, dict) or payload.get("v") != _VERSION:
            raise InvalidCursorError("cursor version is not supported")
        offset, expires, expected = payload.get("o"), payload.get("e"), payload.get("f")
        if (
            not isinstance(offset, int)
            or isinstance(offset, bool)
            or not 0 <= offset <= MAX_OFFSET
            or not isinstance(expires, int)
            or isinstance(expires, bool)
            or not isinstance(expected, str)
        ):
            raise InvalidCursorError("cursor payload is malformed")
        if expires < self._now():
            raise InvalidCursorError("cursor has expired")
        if not hmac.compare_digest(expected, fingerprint(operation_id, filters)):
            raise InvalidCursorError("cursor does not belong to this query")
        return offset
