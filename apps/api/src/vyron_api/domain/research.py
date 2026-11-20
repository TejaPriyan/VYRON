"""Parse and validate structured research output from an AI provider.

Model output is untrusted input. This module never executes or interprets it beyond JSON parsing and
strict shape checks. The model cannot assign provenance: a claim is ``sourced`` only if it cites
evidence ids that were actually retrieved for this run; citations to unknown ids are discarded and
recorded, and a claim left without valid evidence is downgraded to ``ai_generated``. Source URLs are
never taken from the model at all, because the model only references evidence ids.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from vyron_api.domain.evidence import Claim
from vyron_api.domain.provenance import Provenance

MAX_OUTPUT_CHARS = 200_000
MAX_ITEMS = 100
MAX_SUMMARY_CHARS = 4000
MAX_TEXT_CHARS = 1000
MAX_ID_CHARS = 200

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(?P<body>.*)\n```$", re.DOTALL | re.IGNORECASE)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ResearchOutputError(ValueError):
    """The model output is not valid structured research output."""


@dataclass(frozen=True, slots=True)
class ConnectionNote:
    source_id: str
    target_id: str
    explanation: str
    provenance: Provenance
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TimelineNote:
    year: int
    text: str
    provenance: Provenance
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResearchResult:
    summary: str
    claims: tuple[Claim, ...]
    connections: tuple[ConnectionNote, ...]
    timeline: tuple[TimelineNote, ...]
    uncertainty: tuple[str, ...]
    cited_evidence_ids: tuple[str, ...]
    rejected_citation_ids: tuple[str, ...]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ResearchOutputError(f"duplicate key in model output: {key!r}")
        result[key] = value
    return result


def _reject_constant(name: str) -> Any:
    raise ResearchOutputError(f"non-finite number in model output: {name}")


def _clean_text(value: Any, field: str, *, max_chars: int) -> str:
    if not isinstance(value, str):
        raise ResearchOutputError(f"{field} must be a string")
    text = _CONTROL_RE.sub("", unicodedata.normalize("NFC", value)).strip()
    if not text:
        raise ResearchOutputError(f"{field} must not be empty")
    if len(text) > max_chars:
        raise ResearchOutputError(f"{field} is longer than {max_chars} characters")
    return text


def _list(value: Any, field: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ResearchOutputError(f"{field} must be a list")
    if len(value) > MAX_ITEMS:
        raise ResearchOutputError(f"{field} has more than {MAX_ITEMS} items")
    return value


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ResearchOutputError(f"{field} must be an object")
    return value


def _evidence_ids(raw: Any, field: str) -> list[str]:
    ids: list[str] = []
    for item in _list(raw, field):
        if not isinstance(item, str) or not item.strip() or len(item) > MAX_ID_CHARS:
            raise ResearchOutputError(f"{field} must contain non-empty id strings")
        ids.append(item.strip())
    return ids


def _resolve_citations(raw_ids: list[str], allowed: Collection[str]) -> tuple[Provenance, tuple[str, ...], list[str]]:
    valid: list[str] = []
    rejected: list[str] = []
    for evidence_id in raw_ids:
        if evidence_id in allowed:
            if evidence_id not in valid:
                valid.append(evidence_id)
        else:
            rejected.append(evidence_id)
    provenance = Provenance.SOURCED if valid else Provenance.AI_GENERATED
    return provenance, tuple(valid), rejected


def _decode(raw: str) -> dict[str, Any]:
    if len(raw) > MAX_OUTPUT_CHARS:
        raise ResearchOutputError("model output is too large")
    text = raw.strip()
    fenced = _FENCE_RE.match(text)
    if fenced:
        text = fenced.group("body").strip()
    try:
        data = json.loads(text, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise ResearchOutputError("model output is not valid JSON") from exc
    except RecursionError as exc:
        raise ResearchOutputError("model output is nested too deeply") from exc
    if not isinstance(data, dict):
        raise ResearchOutputError("model output must be a JSON object")
    return data


def parse_research_output(raw: str, allowed_evidence_ids: Collection[str]) -> ResearchResult:
    """Validate ``raw`` and resolve every citation against ``allowed_evidence_ids``."""
    data = _decode(raw)
    summary = _clean_text(data.get("summary"), "summary", max_chars=MAX_SUMMARY_CHARS)
    rejected_all: list[str] = []
    cited: list[str] = []

    def resolve(raw_ids: list[str]) -> tuple[Provenance, tuple[str, ...]]:
        provenance, valid, rejected = _resolve_citations(raw_ids, allowed_evidence_ids)
        rejected_all.extend(r for r in rejected if r not in rejected_all)
        cited.extend(v for v in valid if v not in cited)
        return provenance, valid

    claims: list[Claim] = []
    for index, item in enumerate(_list(data.get("claims"), "claims")):
        entry = _mapping(item, f"claims[{index}]")
        text = _clean_text(entry.get("text"), f"claims[{index}].text", max_chars=MAX_TEXT_CHARS)
        confidence = entry.get("confidence")
        if confidence is not None and (
            isinstance(confidence, bool) or not isinstance(confidence, int | float) or not 0.0 <= confidence <= 1.0
        ):
            raise ResearchOutputError(f"claims[{index}].confidence must be a number from 0 to 1")
        provenance, valid = resolve(_evidence_ids(entry.get("evidence_ids"), f"claims[{index}]"))
        claims.append(
            Claim(
                text,
                provenance,
                valid,
                None if confidence is None else float(confidence),
            )
        )

    connections: list[ConnectionNote] = []
    for index, item in enumerate(_list(data.get("connections"), "connections")):
        entry = _mapping(item, f"connections[{index}]")
        provenance, valid = resolve(_evidence_ids(entry.get("evidence_ids"), f"connections[{index}]"))
        connections.append(
            ConnectionNote(
                _clean_text(entry.get("source"), f"connections[{index}].source", max_chars=MAX_ID_CHARS),
                _clean_text(entry.get("target"), f"connections[{index}].target", max_chars=MAX_ID_CHARS),
                _clean_text(
                    entry.get("explanation"),
                    f"connections[{index}].explanation",
                    max_chars=MAX_TEXT_CHARS,
                ),
                provenance,
                valid,
            )
        )

    timeline: list[TimelineNote] = []
    for index, item in enumerate(_list(data.get("timeline"), "timeline")):
        entry = _mapping(item, f"timeline[{index}]")
        year = entry.get("year")
        if isinstance(year, bool) or not isinstance(year, int) or not 1800 <= year <= 2200:
            raise ResearchOutputError(f"timeline[{index}].year must be an integer from 1800 to 2200")
        provenance, valid = resolve(_evidence_ids(entry.get("evidence_ids"), f"timeline[{index}]"))
        timeline.append(
            TimelineNote(
                year,
                _clean_text(entry.get("text"), f"timeline[{index}].text", max_chars=MAX_TEXT_CHARS),
                provenance,
                valid,
            )
        )

    uncertainty = tuple(
        _clean_text(item, f"uncertainty[{index}]", max_chars=MAX_TEXT_CHARS)
        for index, item in enumerate(_list(data.get("uncertainty"), "uncertainty"))
    )

    return ResearchResult(
        summary=summary,
        claims=tuple(claims),
        connections=tuple(connections),
        timeline=tuple(timeline),
        uncertainty=uncertainty,
        cited_evidence_ids=tuple(cited),
        rejected_citation_ids=tuple(rejected_all),
    )
