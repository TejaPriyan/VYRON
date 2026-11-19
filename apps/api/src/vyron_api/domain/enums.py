"""Closed vocabularies shared by the domain, the SQL schema and the API contract.

Each enum here is mirrored by a PostgreSQL ENUM and by the OpenAPI schemas; tests compare them.
"""

from __future__ import annotations

from enum import StrEnum


class SourceType(StrEnum):
    OFFICIAL_DOCS = "official_docs"
    REPOSITORY = "repository"
    PAPER = "paper"
    NEWS = "news"
    BLOG = "blog"
    ARCHIVE = "archive"
    DATASET = "dataset"
    OTHER = "other"


class Reliability(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    TERTIARY = "tertiary"
    UNVERIFIED = "unverified"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DatePrecision(StrEnum):
    DAY = "day"
    MONTH = "month"
    YEAR = "year"
    DECADE = "decade"


class MapMode(StrEnum):
    ECOSYSTEM = "ecosystem"
    DEPENDENCIES = "dependencies"
    COMPANIES = "companies"
    AI = "ai"
    OPEN_SOURCE = "open_source"
    RESEARCH = "research"
    TIMELINE = "timeline"


class ResearchKind(StrEnum):
    EXPLAIN_ENTITY = "explain_entity"
    EXPLAIN_RELATIONSHIP = "explain_relationship"
    COMPARE = "compare"
    HISTORICAL = "historical"
    EXPLAIN_REPOSITORY = "explain_repository"
    RISK = "risk"
    RESEARCH = "research"
    GRAPH_QUESTION = "graph_question"
    FUTURE_SCENARIO = "future_scenario"


class ResearchStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
