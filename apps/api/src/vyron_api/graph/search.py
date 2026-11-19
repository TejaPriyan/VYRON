"""Ranking for entity search: exact, prefix, token-prefix, substring, then typo-tolerant fuzzy.

Pure and deterministic. This is the reference behavior; PostgreSQL full-text/trigram search must be
tested against the same expectations when it is added.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from difflib import SequenceMatcher

MIN_SCORE = 20.0
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    """Casefold, strip accents, spell out ``+``/``#`` (so C++ != C), collapse punctuation."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    spelled = stripped.casefold().replace("+", "plus").replace("#", "sharp")
    return _NON_ALNUM_RE.sub(" ", spelled).strip()


def score_text(query: str, candidate: str) -> float:
    """Score one already-normalized ``query`` against one normalized ``candidate`` (0 = no match)."""
    if not query or not candidate:
        return 0.0
    if query == candidate:
        return 100.0
    query_compact = query.replace(" ", "")
    candidate_compact = candidate.replace(" ", "")
    if query_compact == candidate_compact:
        return 98.0
    if candidate.startswith(query) or candidate_compact.startswith(query_compact):
        return 80.0
    candidate_tokens = candidate.split()
    if all(any(token.startswith(part) for token in candidate_tokens) for part in query.split()):
        return 70.0
    if query in candidate or query_compact in candidate_compact:
        return 40.0
    best = 0.0
    if len(query_compact) >= 3:
        comparisons = [candidate_compact, *candidate_tokens]
        best = max(SequenceMatcher(None, query_compact, other).ratio() for other in comparisons)
    if best >= 0.8:
        return 20.0 + 15.0 * (best - 0.8) / 0.2
    return 0.0


def score_entity(query: str, name: str, aliases: Iterable[str] = (), slug: str = "") -> float:
    """Best score across the name, aliases (slightly discounted) and slug."""
    normalized_query = normalize(query)
    if not normalized_query:
        return 0.0
    best = score_text(normalized_query, normalize(name))
    for alias in aliases:
        best = max(best, 0.95 * score_text(normalized_query, normalize(alias)))
    if slug:
        best = max(best, 0.9 * score_text(normalized_query, normalize(slug)))
    return best if best >= MIN_SCORE else 0.0
