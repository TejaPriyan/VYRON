"""Check VYRON's GitHub path against the real api.github.com, and save real responses to build on.

    python3 scripts/live_github_check.py                       # a few requests against the real GitHub
    python3 scripts/live_github_check.py --capture captures    # also save what GitHub sent, as fixtures
    python3 scripts/live_github_check.py --activity            # also check the contributors / latest-release read
    python3 scripts/live_github_check.py --flow                # replay the page's own sequence (takes ~2.5 minutes)
    GITHUB_TOKEN=... python3 scripts/live_github_check.py      # 5,000 requests an hour instead of 60

WHY THIS EXISTS. The build sandbox VYRON was written in cannot reach GitHub, so every other test of the live
path runs against ``scripts/standin_github.py``, whose answers are fixture data written from GitHub's
documentation. Those tests show VYRON handles those shapes; they cannot show GitHub sends them. This script is
the check against the real thing, and the live path stays UNVERIFIED until someone has run it on a machine that
can reach GitHub and seen it pass.

``--activity`` adds the opt-in second read (top contributors and the latest release). It costs two or three more of
GitHub's requests per repository, so it is off by default; with ``--capture`` it also saves GitHub's raw answers
for those two endpoints, which no fixture has yet. Those answers are the part of VYRON that has only ever been
tested against shapes written from the documentation.

``--capture DIR --stats`` also saves the answers for the last year of commit activity
(``/repos/{owner}/{repo}/stats/commit_activity``) of each ``--repo``. GitHub's documentation says it answers 202
while it computes the statistics and 200 when asked again a little later, so it asks, waits ``--stats-wait``
seconds and asks again, up to ``--stats-attempts`` times, and saves every attempt as its own file with the time
since the first. It only saves what GitHub sends: nothing in VYRON reads commit activity yet, and none will be
written until a real answer has been seen. Each attempt costs one request.

``--flow`` replays what the repository page does, on a fresh cache and with a recorder on the transport: analyse a
repository, wait past the minute VYRON treats a copy as current, load the contributors and release, wait again,
load them again. For each step it lists the requests VYRON sent (asked "has this changed?" or asked outright, and
GitHub's status) and checks that the freshness label VYRON gave ("fresh", shown as "Live", or "revalidated", shown
as "Confirmed") is the one those answers justify. It exists because a screenshot of the page cannot show whether a
"Confirmed" came from a bug or from every part really being unchanged; this can. It costs about eight requests and
about two and a half minutes (two real waits), so it is off by default and uses only the first ``--repo``.

It drives VYRON's own application in-process, so it exercises the same provider, cache, validation and error
mapping the server uses, and it checks the answers against the API's own response schemas. It never prints the
token, never saves it, and sends it only to api.github.com.

Exit status: 0 every check passed, 1 a check failed, 2 the script could not run (GitHub unreachable, bad
arguments). A pass against ``--base-url`` (a local stand-in) says nothing about GitHub and the output says so.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "apps" / "api" / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))

from vyron_api.adapters.memory_cache import InMemoryTTLCache  # noqa: E402
from vyron_api.api.app import Request  # noqa: E402
from vyron_api.api.bootstrap import build_seed_application  # noqa: E402
from vyron_api.api.hostcheck import is_loopback_bind  # noqa: E402
from vyron_api.api.schemas import COMPONENTS  # noqa: E402
from vyron_api.api.validate import validate  # noqa: E402
from vyron_api.github.normalize import TOP_CONTRIBUTORS  # noqa: E402
from vyron_api.github.provider import FRESH_FOR_SECONDS, LiveGitHubProvider, build_live_provider  # noqa: E402
from vyron_api.github.retry import RawResponse  # noqa: E402
from vyron_api.github.transport import DEFAULT_BASE_URL, UrllibGitHubTransport  # noqa: E402
from vyron_api.providers.github import GitHubError  # noqa: E402

REAL_DEFAULTS = ("pallets/flask", "vyron-check-owner-that-does-not-exist/no-such-repository")
STANDIN_DEFAULTS = ("fixture-org/example-repo", "fixture-org/missing-repo")
#: Headers worth keeping in a capture. Nothing that could identify the caller is on the list.
CAPTURE_HEADERS = (
    "content-type", "cache-control", "etag", "last-modified", "link", "location", "x-github-api-version-selected",
    "x-github-media-type",
    "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset", "x-ratelimit-resource", "x-ratelimit-used",
    "retry-after",
)  # fmt: skip
CLOCK_TOLERANCE = timedelta(minutes=5)
STATS_PATH = "/stats/commit_activity"


@dataclass
class Report:
    out: TextIO
    results: list[tuple[str, str]] = field(default_factory=list)  # (PASS|FAIL|SKIP, label)

    def say(self, line: str = "") -> None:
        print(line, file=self.out)

    def check(self, ok: bool, label: str, detail: str = "") -> bool:
        status = "PASS" if ok else "FAIL"
        self.results.append((status, label))
        self.say(f"  {status}  {label}" + (f"  ({detail})" if detail and not ok else ""))
        return ok

    def skip(self, label: str, why: str) -> None:
        self.results.append(("SKIP", label))
        self.say(f"  SKIP  {label}  ({why})")

    @property
    def failed(self) -> int:
        return sum(1 for status, _ in self.results if status == "FAIL")

    @property
    def passed(self) -> int:
        return sum(1 for status, _ in self.results if status == "PASS")


class Api:
    """VYRON's application, called in-process."""

    def __init__(self, application: Any) -> None:
        self._application = application

    def get(self, path: str) -> tuple[int, Any]:
        response = asyncio.run(self._application.handle(Request("GET", path, "", {})))
        return response.status, (json.loads(response.body) if response.body else None)


def issues_of(instance: Any, schema_name: str) -> list[str]:
    return [f"{i.path}: {i.message}" for i in validate(instance, COMPONENTS[schema_name], COMPONENTS)]


def parse_time(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def split_repo(text: str) -> tuple[str, str]:
    owner, _, name = text.partition("/")
    if not owner or not name or "/" in name:
        raise ValueError(f"{text!r} is not owner/name")
    return owner, name


def describe_failure(status: int, body: Any) -> str:
    error = body.get("error", {}) if isinstance(body, dict) else {}
    return f"HTTP {status}, {error.get('code', 'no error code')}: {error.get('message', 'no message')}"


def hint_for(status: int, body: Any) -> str:
    code = body.get("error", {}).get("code") if isinstance(body, dict) else None
    if code == "upstream_rate_limited":
        return "GitHub's rate limit is used up. Wait for the reset time it reports, or set GITHUB_TOKEN."
    if code == "upstream_error":
        return (
            "VYRON could not get a usable answer from GitHub. Check that this machine can open "
            "https://api.github.com in a browser (a proxy, VPN or firewall may be in the way), and that "
            "GITHUB_TOKEN, if set, is valid."
        )
    if code == "service_unavailable":
        return "GitHub access is switched off in this application."
    message = body.get("error", {}).get("message", "") if isinstance(body, dict) else ""
    if code == "not_found" and "renamed or moved" in message:
        return (
            "GitHub answered with a redirect: the repository was renamed or moved. VYRON does not follow it. "
            "Open the repository on github.com to see its current name and use that."
        )
    return "See the message above."


def _licence_text(body: Mapping[str, Any]) -> str:
    """As the page says it: an identifier, GitHub's own name for a licence it could not name, or nothing."""
    if body.get("license_spdx"):
        return str(body["license_spdx"])
    if body.get("license_name"):
        return f"{body['license_name']} (no standard identifier)"
    return "none identified by GitHub"


def summarize(body: Mapping[str, Any]) -> str:
    languages = body.get("languages", {})
    return (
        f"{body['full_name']}: {body['stars']:,} stars, {body['forks']:,} forks, {body['open_issues']:,} open issues "
        f"and pull requests, default branch {body['default_branch']}, "
        f"license {_licence_text(body)}, {len(languages)} languages, "
        f"last push {body.get('pushed_at') or 'unknown'}"
    )


def check_repository(report: Report, api: Api, base: str, requested: str) -> None:
    owner, name = split_repo(requested)
    path = f"/api/v1/github/repositories/{owner}/{name}"
    report.say(f"\n{requested}")
    status, body = api.get(path)
    if not report.check(status == 200, "the analysis route answers 200", describe_failure(status, body)):
        report.say(f"        {hint_for(status, body)}")
        return
    problems = issues_of(body, "RepositoryAnalysis")
    report.check(not problems, "the answer matches the API's own response schema", "; ".join(problems[:3]))
    report.check(body["provenance"] == "sourced", "it is labelled 'sourced'", f"got {body['provenance']!r}")
    report.check(
        body["full_name"].casefold() == requested.casefold(), "GitHub's repository is the one asked for",
        f"asked for {requested}, got {body['full_name']}",
    )  # fmt: skip
    report.check(
        body["cache_status"] == "fresh", "the first read is fresh (it came from GitHub, not a cache)",
        f"got {body['cache_status']!r}",
    )  # fmt: skip
    wanted = f"{base}/repos/"
    report.check(
        all(url.startswith(wanted) for url in body["source_urls"]), "the evidence URLs are GitHub's API URLs",
        ", ".join(body["source_urls"]),
    )  # fmt: skip
    fetched = parse_time(body["fetched_at"])
    now = datetime.now(timezone.utc)
    report.check(
        fetched is not None and abs(now - fetched) < CLOCK_TOLERANCE, "'fetched_at' is within five minutes of now",
        f"fetched_at {body['fetched_at']}, local time {now.isoformat()}",
    )  # fmt: skip
    report.say(f"        {summarize(body)}")

    before = api.get("/api/v1/github/rate-limit")[1]
    again_status, again = api.get(path)
    after = api.get("/api/v1/github/rate-limit")[1]
    report.check(again_status == 200, "a second request answers 200", describe_failure(again_status, again))
    if again_status == 200:
        report.check(
            again["fetched_at"] == body["fetched_at"] and again["cache_status"] == "fresh",
            "the second request is served from VYRON's cache, with the same 'fetched_at'",
            f"first {body['fetched_at']}, second {again['fetched_at']} ({again['cache_status']})",
        )
    if isinstance(before, dict) and isinstance(after, dict) and None not in (before["remaining"], after["remaining"]):
        report.check(
            before["remaining"] == after["remaining"], "the second request did not use any of GitHub's quota",
            f"remaining went from {before['remaining']} to {after['remaining']}",
        )  # fmt: skip


def quota_remaining(api: Api) -> int | None:
    status, body = api.get("/api/v1/github/rate-limit")
    if status == 200 and isinstance(body, dict) and isinstance(body.get("remaining"), int):
        return int(body["remaining"])
    return None


def describe_activity(body: Mapping[str, Any]) -> str:
    people = ", ".join(f"{c['login']} ({c['contributions']:,})" for c in body["contributors"][:3]) or "no contributors"
    release = body["latest_release"]
    if release is None:
        shown = "no published release"
    else:
        shown = f"latest release {release['tag_name']} published {release['published_at'] or 'at an unknown time'}"
    return f"contributors {body['contributors_status']}: {people}; {shown}"


def check_activity(report: Report, api: Api, base: str, requested: str) -> None:
    """The opt-in contributors and latest-release read. Passes whichever of GitHub's answers it got, if coherent."""
    owner, name = split_repo(requested)
    path = f"/api/v1/github/repositories/{owner}/{name}/activity"
    report.say(f"\n{requested}: contributors and latest release")
    before = quota_remaining(api)
    status, body = api.get(path)
    after = quota_remaining(api)
    if not report.check(status == 200, "the activity route answers 200", describe_failure(status, body)):
        report.say(f"        {hint_for(status, body)}")
        return
    problems = issues_of(body, "RepositoryActivity")
    report.check(not problems, "the answer matches the API's own response schema", "; ".join(problems[:3]))
    report.check(body["provenance"] == "sourced", "it is labelled 'sourced'", f"got {body['provenance']!r}")
    report.check(
        body["full_name"].casefold() == requested.casefold(), "GitHub's repository is the one asked for",
        f"asked for {requested}, got {body['full_name']}",
    )  # fmt: skip
    wanted = f"{base}/repos/"
    report.check(
        len(body["source_urls"]) == 3 and all(url.startswith(wanted) for url in body["source_urls"]),
        "the evidence is three GitHub API URLs (repository, contributors, latest release)",
        ", ".join(body["source_urls"]),
    )  # fmt: skip
    people = body["contributors"]
    counts = [c["contributions"] for c in people]
    coherent = (
        (body["contributors_status"] == "listed" and 0 < len(people) <= TOP_CONTRIBUTORS)
        or (body["contributors_status"] in ("none", "declined") and not people)
    ) and counts == sorted(counts, reverse=True)
    report.check(
        coherent, "the contributor list agrees with its status and is ordered by contributions",
        f"status {body['contributors_status']!r}, counts {counts}",
    )  # fmt: skip
    report.check(
        all(c["profile_url"] is None or c["profile_url"] == f"https://github.com/{c['login']}" for c in people)
        and all(c["profile_url"] is None for c in people if c["is_bot"]),
        "profile links are github.com/<login>, and bots have none",
    )
    release = body["latest_release"]
    if release is not None:
        prefix = f"https://github.com/{body['full_name']}/releases".casefold()
        published = parse_time(release["published_at"]) if release["published_at"] else None
        report.check(
            release["html_url"].casefold().startswith(prefix),
            "the release link is on this repository's releases page", release["html_url"],
        )  # fmt: skip
        report.check(
            published is None or published <= datetime.now(timezone.utc) + CLOCK_TOLERANCE,
            "the release was not published in the future", str(release["published_at"]),
        )  # fmt: skip
    fetched = parse_time(body["fetched_at"])
    now = datetime.now(timezone.utc)
    report.check(
        fetched is not None and abs(now - fetched) < CLOCK_TOLERANCE, "'fetched_at' is within five minutes of now",
        f"fetched_at {body['fetched_at']}, local time {now.isoformat()}",
    )  # fmt: skip
    report.say(f"        {describe_activity(body)}")

    if before is None or after is None:
        report.skip("the read cost two or three of GitHub's requests", "GitHub did not report a quota")
    else:
        report.check(
            before - after in (2, 3), "the read cost two or three of GitHub's requests (documented as 2, or 3 when "
            "the repository copy is over a minute old)", f"remaining went from {before} to {after}",
        )  # fmt: skip

    cacheable = body["contributors_status"] == "listed" and release is not None
    if not cacheable:
        report.skip(
            "a second read is served from VYRON's cache",
            "GitHub's answer was not a 200 for every part, and VYRON never stores those, so it asks again",
        )
        return
    again_status, again = api.get(path)
    after_again = quota_remaining(api)
    report.check(again_status == 200, "a second read answers 200", describe_failure(again_status, again))
    if again_status == 200:
        report.check(
            again["fetched_at"] == body["fetched_at"] and again["cache_status"] == "fresh",
            "the second read is served from VYRON's cache, with the same 'fetched_at'",
            f"first {body['fetched_at']}, second {again['fetched_at']} ({again['cache_status']})",
        )
        if after is not None and after_again is not None:
            report.check(
                after == after_again, "the second read did not use any of GitHub's quota",
                f"remaining went from {after} to {after_again}",
            )  # fmt: skip


def check_missing_activity(report: Report, api: Api, requested: str) -> None:
    owner, name = split_repo(requested)
    status, body = api.get(f"/api/v1/github/repositories/{owner}/{name}/activity")
    if status == 200:
        report.skip("the activity read of a repository that does not exist answers 404", "it exists")
        return
    code = body.get("error", {}).get("code") if isinstance(body, dict) else None
    report.check(
        status == 404 and code == "not_found",
        "the activity read of a repository that does not exist answers 404 not_found", describe_failure(status, body),
    )  # fmt: skip


def check_missing(report: Report, api: Api, requested: str) -> None:
    owner, name = split_repo(requested)
    report.say(f"\n{requested} (should not exist)")
    status, body = api.get(f"/api/v1/github/repositories/{owner}/{name}")
    if status == 200:
        report.skip("a repository that does not exist answers 404", "it exists; give --missing a name that doesn't")
        return
    code = body.get("error", {}).get("code") if isinstance(body, dict) else None
    report.check(
        status == 404 and code == "not_found", "a repository that does not exist answers 404 not_found",
        describe_failure(status, body),
    )  # fmt: skip


@dataclass(frozen=True)
class Exchange:
    """One request VYRON sent to GitHub during the flow check, and what GitHub answered."""

    path: str
    conditional: bool  # it asked "has this changed since the copy I hold?" rather than asking outright
    status: int
    remaining: str | None  # the quota GitHub said was left after this request


class RecordingTransport:
    """Wraps a transport and notes every request it makes. It changes nothing about what is sent or returned."""

    def __init__(self, inner: UrllibGitHubTransport) -> None:
        self._inner = inner
        self.log: list[Exchange] = []

    def url_for(self, path_or_url: str) -> str:
        return self._inner.url_for(path_or_url)

    async def get(self, path_or_url: str, extra_headers: Mapping[str, str] | None = None) -> RawResponse:
        response = await self._inner.get(path_or_url, extra_headers)
        asked = {name.lower() for name in (extra_headers or {})}
        self.log.append(Exchange(
            path_or_url, bool(asked & {"if-none-match", "if-modified-since"}), response.status,
            response.headers.get("x-ratelimit-remaining"),
        ))  # fmt: skip
        return response


class FlowClock:
    """VYRON's clock for the flow check. It really waits, except against a stand-in, where it may skip ahead."""

    def __init__(self, *, simulated: bool) -> None:
        self.simulated = simulated
        self._skipped = 0.0

    def now(self) -> float:
        return time.time() + self._skipped

    def wait(self, seconds: float, out: TextIO) -> None:
        if self.simulated:
            self._skipped += seconds
        else:
            out.flush()  # the person running this should see the line before the pause
            time.sleep(seconds)


def expected_label(exchanges: Sequence[Exchange]) -> str:
    """The freshness label GitHub's answers justify, worked out from them alone.

    "Nothing had changed" (``revalidated``) is true only when every request in the read was answered 304. Any
    other answer (new data, or a 403 or 404 that is reported and never stored) means something was read from
    GitHub just now, so the read is ``fresh``. A request is judged by its last answer, so a retry is not double
    counted. This restates the rule independently of the provider so the two can disagree.
    """
    last = {exchange.path: exchange.status for exchange in exchanges}
    return "revalidated" if last and all(status == 304 for status in last.values()) else "fresh"


def describe_exchange(exchange: Exchange) -> str:
    asked = "asked if it changed" if exchange.conditional else "asked outright"
    left = f", {exchange.remaining} left" if exchange.remaining is not None else ""
    return f"        GET {exchange.path}  ({asked})  ->  {exchange.status}{left}"


def check_flow(
    report: Report, requested: str, *, base: str, token: str | None, timeout: float, wait_seconds: float,
    simulated: bool,
) -> None:  # fmt: skip
    """Replay the repository page's sequence against a fresh cache and check each label against GitHub's answers."""
    owner, name = split_repo(requested)
    analysis = f"/api/v1/github/repositories/{owner}/{name}"
    repo_path = f"/repos/{owner}/{name}".casefold()
    transport = RecordingTransport(UrllibGitHubTransport(
        token=token, base_url=base, timeout=timeout, allow_local_test_server=base != DEFAULT_BASE_URL,
    ))  # fmt: skip
    clock = FlowClock(simulated=simulated)
    api = Api(build_seed_application(
        github=LiveGitHubProvider(transport, InMemoryTTLCache(), clock=clock.now), rate_limit=False,
    ))  # fmt: skip
    how = "simulated: VYRON's clock was moved on; a stand-in only" if simulated else "real"
    report.say(f"\n{requested}: the page's own sequence, on a fresh cache")
    report.say("  The page shows 'Live' for a read from GitHub and 'Confirmed' when everything was unchanged.")
    report.say(
        f"  Waits are {wait_seconds:g} s ({how}); VYRON treats a copy under {FRESH_FOR_SECONDS:g} s old as current."
    )

    def step(title: str, path: str) -> tuple[int, Any, list[Exchange]]:
        report.say(f"\n  {title}")
        start = len(transport.log)
        status, body = api.get(path)
        sent = transport.log[start:]
        for exchange in sent:
            report.say(describe_exchange(exchange))
        return status, body, sent

    def labelled(number: int, sent: Sequence[Exchange], body: Mapping[str, Any]) -> bool:
        return report.check(
            body["cache_status"] == expected_label(sent),
            f"step {number}: the label {body['cache_status']!r} is the one GitHub's answers justify "
            f"({expected_label(sent)!r})",
            "answers were " + (", ".join(str(e.status) for e in sent) or "none (nothing was asked)"),
        )  # fmt: skip

    status, body, sent = step("step 1: analyse the repository", analysis)
    if not report.check(status == 200, "step 1: the analysis route answers 200", describe_failure(status, body)):
        report.say(f"        {hint_for(status, body)}")
        return
    labelled(1, sent, body)
    stored = {e.path for e in sent if e.status == 200}

    report.say(f"\n  waiting {wait_seconds:g} s")
    clock.wait(wait_seconds, report.out)
    status, body, sent = step(
        "step 2: load the contributors and release (the repository copy is now over a minute old)",
        f"{analysis}/activity",
    )
    if not report.check(status == 200, "step 2: the activity route answers 200", describe_failure(status, body)):
        report.say(f"        {hint_for(status, body)}")
        return
    report.check(
        body["cache_status"] == "fresh",
        "step 2: the label is 'fresh' (the page says 'Live'): the contributors and release were first reads",
        f"got {body['cache_status']!r}",
    )  # fmt: skip
    labelled(2, sent, body)
    repo_asks = [e for e in sent if e.path.casefold() == repo_path]
    report.check(
        bool(repo_asks) and repo_asks[0].conditional,
        "step 2: the repository copy was confirmed with a conditional request, not downloaded again",
        "no request for the repository" if not repo_asks else "it was asked for outright",
    )  # fmt: skip
    stored |= {e.path for e in sent if e.status == 200}

    report.say(f"\n  waiting {wait_seconds:g} s")
    clock.wait(wait_seconds, report.out)
    status, body, sent = step("step 3: load the contributors and release again", f"{analysis}/activity")
    if not report.check(status == 200, "step 3: the activity route answers 200", describe_failure(status, body)):
        report.say(f"        {hint_for(status, body)}")
        return
    labelled(3, sent, body)
    held = [e for e in sent if e.path in stored]
    report.check(
        bool(held) and all(e.conditional for e in held),
        "step 3: every copy VYRON held was confirmed with a conditional request, not downloaded again",
        "; ".join(f"{e.path} asked outright" for e in held if not e.conditional) or "nothing was held",
    )  # fmt: skip
    unchanged = ", ".join(e.path for e in sent if e.status == 304) or "none"
    report.say(f"        answered 304 (unchanged): {unchanged}")
    left = [e.remaining for e in transport.log if e.remaining is not None]
    if len(left) >= 2:
        report.say(
            f"        quota: {left[0]} left after the first request, {left[-1]} after the last, "
            f"{len(transport.log)} requests sent (reported, not judged)"
        )


def quota_line(body: Mapping[str, Any]) -> str:
    if body.get("remaining") is None:
        return "GitHub did not report a quota"
    return f"{body['remaining']} of {body['limit']} requests left until {body.get('reset_at')}"


def capture_file_name(path: str) -> str:
    """A file name for a request path. Windows file names cannot hold ``?`` or ``=``, so a query is folded in."""
    route, _, query = path.partition("?")
    name = route.strip("/").replace("/", "__")
    if query:
        name += "__" + re.sub(r"[^A-Za-z0-9._-]", "-", query)
    return name + ".json"


def response_record(path: str, response: RawResponse) -> dict[str, Any]:
    """One saved response: its request, status, allow-listed headers and body (JSON, or the text if it is not)."""
    text = response.body.decode("utf-8", errors="replace")
    try:
        body: Any = json.loads(text)
        key = "body"
    except ValueError:
        body, key = text, "body_text"
    return {
        "request": path,
        "status": response.status,
        "headers": {k: v for k, v in sorted(response.headers.items()) if k in CAPTURE_HEADERS},
        key: body,
    }


def capture_stats(
    out_dir: Path, transport: UrllibGitHubTransport, repos: Sequence[str], report: Report, *,
    wait_seconds: float, attempts: int, sleep: Any = time.sleep,
) -> list[str]:  # fmt: skip
    """Ask for each repository's last year of commit activity until GitHub stops saying 202, saving every attempt.

    The answer is not asked through VYRON's provider or cache: each attempt goes to GitHub outright, so what is
    saved is what GitHub sent. A file is ``<path>__attempt-N.json``, the usual record plus ``attempt`` and
    ``seconds_since_first_attempt``, so how long GitHub took to settle is on record.
    """
    written: list[str] = []

    async def fetch(path: str) -> RawResponse:
        return await transport.get(path)

    for requested in repos:
        owner, name = split_repo(requested)
        path = f"/repos/{owner}/{name}{STATS_PATH}"
        started = time.monotonic()
        settled = False
        for attempt in range(1, attempts + 1):
            if attempt > 1:
                report.out.flush()  # the person running this should see the line before the pause
                sleep(wait_seconds)
            try:
                response = asyncio.run(fetch(path))
            except (OSError, GitHubError) as exc:
                report.say(f"  could not capture {path} (attempt {attempt}): {type(exc).__name__}")
                break
            record = response_record(path, response)
            record["attempt"] = attempt
            record["seconds_since_first_attempt"] = round(time.monotonic() - started, 1)
            file_name = capture_file_name(path)[: -len(".json")] + f"__attempt-{attempt}.json"
            (out_dir / file_name).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            written.append(file_name)
            report.say(f"  {requested} commit activity, attempt {attempt}: HTTP {response.status}")
            if response.status != 202:
                settled = True
                break
        else:
            report.say(f"  {requested}: still 202 after {attempts} attempts; that is what GitHub said, and is saved")
        if settled:
            report.say(f"  {requested}: settled after {attempt} attempt{'s' if attempt != 1 else ''}")
    return written


def capture(
    out_dir: Path,
    transport: UrllibGitHubTransport,
    base: str,
    repos: Sequence[str],
    missing: str,
    report: Report,
    *,
    activity: bool = False,
    stats: bool = False,
    stats_wait: float = 5.0,
    stats_attempts: int = 6,
) -> None:
    """Save what GitHub sent, one file per response, so later work can be tested against real shapes."""
    out_dir.mkdir(parents=True, exist_ok=True)
    real = base == DEFAULT_BASE_URL
    paths = ["/rate_limit"]
    for requested in (*repos, missing):
        owner, name = split_repo(requested)
        paths.append(f"/repos/{owner}/{name}")
        if requested != missing:
            paths.append(f"/repos/{owner}/{name}/languages")
            if activity:
                paths.append(f"/repos/{owner}/{name}/contributors?per_page={TOP_CONTRIBUTORS}")
                paths.append(f"/repos/{owner}/{name}/releases/latest")
    written: list[str] = []

    async def fetch(path: str) -> Any:
        return await transport.get(path)

    for path in paths:
        try:
            response = asyncio.run(fetch(path))
        except (OSError, GitHubError) as exc:
            report.say(f"  could not capture {path}: {type(exc).__name__}")
            continue
        record = response_record(path, response)
        name = capture_file_name(path)
        (out_dir / name).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(name)
    if stats:
        written.extend(capture_stats(
            out_dir, transport, repos, report, wait_seconds=stats_wait, attempts=stats_attempts,
        ))  # fmt: skip
    manifest = {
        "data": "REAL GITHUB DATA" if real else "FIXTURE DATA, NOT FROM GITHUB (a local stand-in)",
        "source": base,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool": "scripts/live_github_check.py",
        "authenticated": transport.authenticated,
        "files": written,
        "note": "Response headers are limited to an allow-list; no credential is ever written here.",
    }
    if stats:
        manifest["stats"] = {
            "endpoint": STATS_PATH, "repositories": list(repos), "wait_seconds": stats_wait,
            "max_attempts": stats_attempts,
        }
    (out_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report.say(f"\nSaved {len(written)} responses and MANIFEST.json to {out_dir}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", action="append", metavar="OWNER/NAME", help="a public repository (repeatable)")
    parser.add_argument("--missing", metavar="OWNER/NAME", help="a name that does not exist, for the 404 check")
    parser.add_argument(
        "--activity", action="store_true",
        help="also check the opt-in contributors and latest-release read (2 or 3 more requests per repository)",
    )  # fmt: skip
    parser.add_argument(
        "--flow", action="store_true",
        help="replay the page's sequence (analyse, wait, load contributors and release, wait, load again) on the "
             "first --repo, checking each freshness label against what GitHub answered (8 requests, 2.5 minutes)",
    )  # fmt: skip
    parser.add_argument(
        "--flow-wait", type=float, default=FRESH_FOR_SECONDS + 5, metavar="SECONDS",
        help=f"how long --flow waits between steps; must be over {FRESH_FOR_SECONDS:g} (default %(default)g)",
    )  # fmt: skip
    parser.add_argument("--simulate-wait", action="store_true", help=argparse.SUPPRESS)  # stand-in only
    parser.add_argument("--capture", metavar="DIR", help="save GitHub's raw responses to DIR as fixtures")
    parser.add_argument(
        "--stats", action="store_true",
        help="with --capture: also save the last year of commit activity, asking again while GitHub says 202",
    )  # fmt: skip
    parser.add_argument(
        "--stats-wait", type=float, default=5.0, metavar="SECONDS",
        help="seconds between asks while GitHub says 202 (0 to 60, default %(default)g)",
    )  # fmt: skip
    parser.add_argument(
        "--stats-attempts", type=int, default=6, metavar="N",
        help="most asks per repository, one request each (1 to 10, default %(default)d)",
    )  # fmt: skip
    parser.add_argument("--timeout", type=float, default=15.0, help="seconds to wait for each GitHub request")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=argparse.SUPPRESS)  # loopback stand-ins only
    return parser


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None, out: TextIO | None = None) -> int:
    out = out or sys.stdout
    environ = os.environ if environ is None else environ
    args = build_parser().parse_args(argv)
    report = Report(out)
    # This script is the reporter: a failure appears as a FAIL line, so the library's own warnings stay quiet.
    logging.getLogger("vyron").addHandler(logging.NullHandler())
    base = args.base_url.rstrip("/")
    real = base == DEFAULT_BASE_URL
    if not real and not (urlsplit(base).scheme == "http" and is_loopback_bind(urlsplit(base).hostname or "")):
        report.say("error: --base-url is only for a local stand-in on a loopback address")
        return 2
    defaults = REAL_DEFAULTS if real else STANDIN_DEFAULTS
    repos = args.repo or [defaults[0]]
    missing = args.missing or defaults[1]
    try:
        for requested in (*repos, missing):
            split_repo(requested)
    except ValueError as exc:
        report.say(f"error: {exc}")
        return 2
    if args.stats and not args.capture:
        report.say("error: --stats saves GitHub's answers, so it needs --capture DIR")
        return 2
    if not (0 <= args.stats_wait <= 60 and 1 <= args.stats_attempts <= 10):
        report.say("error: --stats-wait must be 0 to 60 seconds and --stats-attempts 1 to 10")
        return 2
    if args.simulate_wait and real:
        report.say("error: --simulate-wait is only for a local stand-in; against GitHub the waits are real")
        return 2
    if args.flow and args.flow_wait <= FRESH_FOR_SECONDS:
        report.say(f"error: --flow-wait must be over {FRESH_FOR_SECONDS:g} seconds, or nothing is asked again")
        return 2
    # A token is for api.github.com. A stand-in never receives one, so no real credential reaches anything else.
    token = (environ.get("GITHUB_TOKEN") or "").strip() or None if real else None
    try:
        provider = build_live_provider(
            token=token, base_url=base, allow_local_test_server=not real, timeout=args.timeout
        )
    except ValueError as exc:
        report.say(f"error: GITHUB_TOKEN is not usable: {exc}")
        return 2

    report.say("VYRON live GitHub check")
    if real:
        report.say(f"  target:  {base} (the real GitHub)")
    else:
        report.say(f"  target:  {base}  STAND-IN, FIXTURE DATA: nothing here comes from GitHub")
    if token:
        report.say("  token:   set (value not shown)")
    elif real:
        report.say("  token:   not set (GitHub allows 60 requests an hour)")
    else:
        report.say("  token:   not sent to a stand-in")

    api = Api(build_seed_application(github=provider, rate_limit=False))
    report.say("\nReaching GitHub")
    status, quota = api.get("/api/v1/github/rate-limit")
    if status != 200:
        report.say(f"  FAIL  could not read GitHub's quota  ({describe_failure(status, quota)})")
        report.say(f"        {hint_for(status, quota)}")
        report.say("\nRESULT: could not run. Nothing was checked.")
        return 2
    report.check(not issues_of(quota, "RateLimitStatus"), "GitHub's quota answers in the API's schema")
    report.say(f"        {quota_line(quota)}")

    for requested in repos:
        check_repository(report, api, base, requested)
        if args.activity:
            check_activity(report, api, base, requested)
    check_missing(report, api, missing)
    if args.activity:
        check_missing_activity(report, api, missing)
    if args.flow:
        check_flow(
            report, repos[0], base=base, token=token, timeout=args.timeout, wait_seconds=args.flow_wait,
            simulated=args.simulate_wait,
        )  # fmt: skip

    if args.capture:
        capture(Path(args.capture), UrllibGitHubTransport(
            token=token, base_url=base, timeout=args.timeout, allow_local_test_server=not real,
        ), base, repos, missing, report, activity=args.activity, stats=args.stats,
            stats_wait=args.stats_wait, stats_attempts=args.stats_attempts)  # fmt: skip

    code, line = verdict(report, real)
    report.say(f"\n{line}")
    return code


def verdict(report: Report, real: bool) -> tuple[int, str]:
    """The exit status and the closing line. Only a pass against the real GitHub may say VERIFIED."""
    total = report.passed + report.failed
    counted = f"{total} check" if total == 1 else f"{total} checks"
    if report.failed:
        return 1, f"RESULT: FAIL, {report.failed} of {total} checks failed."
    if real:
        return 0, (
            f"RESULT: PASS, {counted} against api.github.com. The live path is VERIFIED for what was checked "
            "here (the repositories above, on this date, from this machine); nothing else is."
        )
    return 0, (
        f"RESULT: PASS, {counted} against a local stand-in. This is fixture data: it does NOT verify "
        "api.github.com, and the live path stays UNVERIFIED."
    )


if __name__ == "__main__":
    raise SystemExit(main())
