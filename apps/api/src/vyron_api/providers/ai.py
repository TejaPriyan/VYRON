from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol


class AIProviderError(Exception):
    """Base class for provider failures the application is expected to handle."""


class AIUnavailableError(AIProviderError):
    """The provider is not configured, unreachable, or rate limited."""


@dataclass(frozen=True, slots=True)
class AIRequest:
    system: str
    prompt: str
    json_schema: Mapping[str, Any] | None = None
    max_output_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class AIResponse:
    text: str
    model: str
    provider: str


class AIProvider(Protocol):
    """Intelligence layer. Never the source of truth: it only sees retrieved evidence."""

    name: str

    async def generate(self, request: AIRequest) -> AIResponse: ...
