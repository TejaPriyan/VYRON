"""Sources, evidence and claims: the trust layer.

A source URL is only ever stored if a provider returned it; this module cannot make one up, it can
only refuse unsafe ones. Citations must point at stored evidence, and a claim only counts as
``sourced`` when it references at least one evidence id.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urlsplit

from vyron_api.domain.enums import Reliability, SourceType
from vyron_api.domain.provenance import Provenance

_MAX_URL_LENGTH = 2048
_MAX_CLAIM_LENGTH = 1000
_PRINTABLE_ASCII_RE = re.compile(r"^[\x21-\x7e]+$")
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")


class InvalidSourceUrlError(ValueError):
    """The URL is not an acceptable public http(s) source."""


def _is_non_public_host(host: str) -> bool:
    lowered = host.lower().rstrip(".")
    if lowered == "localhost" or lowered.endswith(_LOCAL_SUFFIXES):
        return True
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        return "." not in lowered  # single-label names are internal hosts
    return not address.is_global


def validate_source_url(url: str) -> str:
    """Return ``url`` unchanged if it is a safe public http(s) URL, otherwise raise."""
    if not url or len(url) > _MAX_URL_LENGTH:
        raise InvalidSourceUrlError("source URL is empty or too long")
    if not _PRINTABLE_ASCII_RE.fullmatch(url):
        raise InvalidSourceUrlError("source URL must be printable ASCII (use punycode/percent-encoding)")
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - raises ValueError for malformed ports
    except ValueError as exc:
        raise InvalidSourceUrlError("source URL is malformed") from exc
    if parts.scheme not in {"http", "https"}:
        raise InvalidSourceUrlError("only http(s) source URLs are accepted")
    if parts.username is not None or parts.password is not None:
        raise InvalidSourceUrlError("credentials in source URLs are not accepted")
    host = parts.hostname
    if not host or _is_non_public_host(host):
        raise InvalidSourceUrlError("source URL must point at a public host")
    return url


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class Source:
    id: str
    url: str
    title: str
    source_type: SourceType
    retrieved_at: datetime
    publisher: str | None = None
    published_on: date | None = None
    reliability: Reliability = Reliability.UNVERIFIED

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("source id must not be empty")
        validate_source_url(self.url)
        if not self.title.strip():
            raise ValueError("source title must not be empty")
        _require_aware(self.retrieved_at, "retrieved_at")


@dataclass(frozen=True, slots=True)
class Evidence:
    """claim -> source -> entity -> timestamp."""

    id: str
    claim: str
    source_id: str
    entity_id: str
    retrieved_at: datetime
    locator: str | None = None

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.source_id.strip() or not self.entity_id.strip():
            raise ValueError("evidence id, source id and entity id must not be empty")
        if not self.claim.strip() or len(self.claim) > _MAX_CLAIM_LENGTH:
            raise ValueError("evidence claim must be 1-1000 characters")
        _require_aware(self.retrieved_at, "retrieved_at")


@dataclass(frozen=True, slots=True)
class Claim:
    text: str
    provenance: Provenance
    evidence_ids: tuple[str, ...] = ()
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("claim text must not be empty")
        if self.provenance.requires_evidence and not self.evidence_ids:
            raise ValueError("a 'sourced' claim must reference at least one evidence id")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("claim confidence must be between 0 and 1")


def unsupported_claims(claims: Iterable[Claim]) -> list[Claim]:
    """Claims the UI must label as not source-backed (everything that is not ``sourced``)."""
    return [claim for claim in claims if claim.provenance is not Provenance.SOURCED]
