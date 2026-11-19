"""Release dates as a package registry records them, turned into a plain list. Standard library only.

Nothing is fetched here: these functions read the document the package reader already has, so the dates cost no
extra request. What a date means differs by registry and is carried with the list, never smoothed over:

* PyPI: the earliest upload time among the files of a version (``releases[version][*].upload_time_iso_8601``). The
  ``releases`` key of PyPI's project JSON is documented as deprecated and may be removed, so its absence is an
  answer in its own right (``None`` here), not an empty list.
* npm: the ``time`` entry the registry holds for a version that is still published. ``time`` also names versions
  that were unpublished; they are counted, not listed.

A version is called a pre-release only from its number (PEP 440 segments for PyPI, a ``-`` suffix for npm). That is
a reading of the string, not something the registry says. Nothing here judges whether a package is maintained.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from vyron_api.registries.errors import RegistryError

MAX_RELEASES = 2000  # the newest are kept; ``omitted_older`` says how many earlier ones are not in the list
MAX_VERSIONS_READ = 50_000  # more than this in one document is refused, not sampled
EARLIEST_PLAUSIBLE = datetime(1991, 1, 1, tzinfo=UTC)  # before the web existed
FUTURE_SLACK = timedelta(days=1)

#: Why a version the registry names is not in the list. ``undated``: no usable date came with it. ``removed``: npm still
#: holds a ``time`` entry for a version that is no longer published. ``implausible_date``: before 1991 or after now.
#: ``unusable``: a version string no registry would issue.
RELEASE_LEFT_OUT_KINDS = ("undated", "removed", "implausible_date", "unusable")
RELEASE_STATUSES = ("normal", "yanked", "deprecated")
DATE_MEANINGS = ("first_file_upload", "publish_time")

_PYPI_PRE = re.compile(r"\d[._-]?(?:dev|a|b|c|rc|alpha|beta|pre|preview)[._-]?\d*(?:[._-]|$)", re.IGNORECASE)
_NPM_PRE = re.compile(r"^[v=]?\d+\.\d+\.\d+-")
_TIME_KEYS = frozenset({"created", "modified", "unpublished"})


class ReleaseDatesMissingError(RegistryError):
    """The registry answered, but its answer carries no release dates for this package."""


@dataclass(frozen=True, slots=True)
class ReleaseRecord:
    version: str
    released_at: datetime  # always timezone-aware, UTC
    pre_release: bool  # from the version number only
    status: str  # one of RELEASE_STATUSES: PyPI "yanked", npm "deprecated", otherwise "normal"


@dataclass(frozen=True, slots=True)
class ReleaseList:
    records: tuple[ReleaseRecord, ...]  # oldest first
    date_meaning: str  # one of DATE_MEANINGS
    omitted_older: int = 0  # releases older than the oldest listed one that were dropped to keep the list bounded
    left_out: tuple[tuple[str, int], ...] = ()  # (kind, count), kinds in RELEASE_LEFT_OUT_KINDS


def parse_timestamp(value: object) -> datetime | None:
    """An ISO 8601 timestamp as UTC. A timestamp with no zone is read as UTC (PyPI's ``upload_time`` is). Else None."""
    if not isinstance(value, str) or not 10 <= len(value) <= 40:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _usable_version(version: object) -> bool:
    return isinstance(version, str) and 0 < len(version) <= 100 and version.isprintable() and version == version.strip()


def pypi_looks_like_pre_release(version: str) -> bool:
    return bool(_PYPI_PRE.search(version.split("+", 1)[0]))


def npm_looks_like_pre_release(version: str) -> bool:
    return bool(_NPM_PRE.match(version))


def _finish(records: list[ReleaseRecord], date_meaning: str, counts: dict[str, int], cap: int) -> ReleaseList:
    records.sort(key=lambda record: (record.released_at, record.version))
    omitted = max(0, len(records) - cap)
    kept = records[omitted:]
    left_out = tuple((kind, counts[kind]) for kind in RELEASE_LEFT_OUT_KINDS if counts.get(kind))
    return ReleaseList(tuple(kept), date_meaning, omitted, left_out)


def _plausible(moment: datetime, now: datetime) -> bool:
    return EARLIEST_PLAUSIBLE <= moment <= now + FUTURE_SLACK


def pypi_release_list(releases: object, *, now: datetime, cap: int = MAX_RELEASES) -> ReleaseList | None:
    """PyPI's ``releases`` object as a list, or ``None`` when the document has no such object."""
    if not isinstance(releases, dict):
        return None
    if len(releases) > MAX_VERSIONS_READ:
        raise RegistryError("PyPI sent release information in an unexpected shape")
    counts = dict.fromkeys(RELEASE_LEFT_OUT_KINDS, 0)
    records: list[ReleaseRecord] = []
    for version, files in releases.items():
        if not _usable_version(version):
            counts["unusable"] += 1
            continue
        moments: list[datetime] = []
        yanked: list[bool] = []
        for item in files if isinstance(files, list) else ():
            if not isinstance(item, dict):
                continue
            moment = parse_timestamp(item.get("upload_time_iso_8601")) or parse_timestamp(item.get("upload_time"))
            if moment is not None:
                moments.append(moment)
                yanked.append(item.get("yanked") is True)
        if not moments:
            counts["undated"] += 1
            continue
        first = min(moments)
        if not _plausible(first, now):
            counts["implausible_date"] += 1
            continue
        records.append(
            ReleaseRecord(version, first, pypi_looks_like_pre_release(version), "yanked" if all(yanked) else "normal")
        )
    return _finish(records, "first_file_upload", counts, cap)


def npm_release_list(times: object, versions: object, *, now: datetime, cap: int = MAX_RELEASES) -> ReleaseList | None:
    """npm's ``time`` and ``versions`` objects as a list, or ``None`` when the document has no ``time`` object."""
    if not isinstance(times, dict) or not isinstance(versions, dict):
        return None
    if len(versions) > MAX_VERSIONS_READ or len(times) > MAX_VERSIONS_READ + 3:
        raise RegistryError("npm sent release information in an unexpected shape")
    counts = dict.fromkeys(RELEASE_LEFT_OUT_KINDS, 0)
    records: list[ReleaseRecord] = []
    for version, declared in versions.items():
        if not _usable_version(version):
            counts["unusable"] += 1
            continue
        moment = parse_timestamp(times.get(version))
        if moment is None:
            counts["undated"] += 1
            continue
        if not _plausible(moment, now):
            counts["implausible_date"] += 1
            continue
        deprecated = isinstance(declared, dict) and bool(declared.get("deprecated"))
        records.append(
            ReleaseRecord(
                version, moment, npm_looks_like_pre_release(version), "deprecated" if deprecated else "normal"
            )
        )
    counts["removed"] = sum(1 for key in times if key not in _TIME_KEYS and key not in versions)
    return _finish(records, "publish_time", counts, cap)
