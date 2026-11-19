"""The last piece of repository analysis: a year of weekly commit counts, which GitHub computes in the background.

The PAYLOADS HERE ARE HAND-WRITTEN (``github_fakes.commit_activity_payload``: real shape, made-up numbers). They show
that VYRON behaves correctly *given* those answers. What GitHub really sent, and VYRON's handling of it, is in
``test_github_real_captures.py`` (``TheCommitActivityGitHubReallySent``).

What the real runs of 2026-11-04 showed, and what this slice is built around: GitHub answers 202 with an empty
body while it computes, a repository it has not computed recently needed about sixteen seconds, and every ask,
202 included, costs a request. So the provider never waits; it reports "computing" and the reader asks again.
"""

import json
import unittest
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from github_fakes import (
    FIRST_FIXTURE_WEEK,
    FIXTURE_REF,
    FakeGitHubProvider,
    FakeTransport,
    commit_activity_payload,
    error,
    languages_payload,
    ok,
    repo_payload,
)
from test_api_app import get, make_app
from vyron_api.adapters.memory_cache import InMemoryTTLCache
from vyron_api.api.schemas import COMPONENTS
from vyron_api.api.validate import validate
from vyron_api.github.normalize import MalformedGitHubPayloadError, normalize_commit_activity
from vyron_api.github.provider import LiveGitHubProvider
from vyron_api.github.retry import RawResponse
from vyron_api.github.url import RepoRef
from vyron_api.providers.github import (
    CacheStatus,
    CommitActivityStatus,
    GitHubForbiddenError,
    GitHubNotFoundError,
    GitHubRateLimitedError,
    GitHubUnavailableError,
)

T0 = 1_792_577_730.0  # 2026-10-21T10:15:30Z
REPO = FIXTURE_REF.api_path
LANGS = f"{REPO}/languages"
STATS = f"{REPO}/stats/commit_activity"
URL = f"/api/v1/github/repositories/{FIXTURE_REF.owner}/{FIXTURE_REF.repo}/commit-activity"
# What GitHub really sent while it was computing: 202, JSON content type, an empty object, no validator.
COMPUTING = RawResponse(
    202, {"content-type": "application/json; charset=utf-8", "x-ratelimit-remaining": "49"}, b"{}"
)
NO_CONTENT = RawResponse(204, {"etag": 'W/"empty"', "x-ratelimit-remaining": "50"}, b"")
NOT_MODIFIED = RawResponse(304, {"x-ratelimit-remaining": "40"}, b"")


def stats_ok(body: Any = None, **headers: str) -> RawResponse:
    """A 200 with the headers the real answer had: a weak ETag and a one-minute public cache-control."""
    sent = {"etag": 'W/"stats-1"', "cache-control": "public, max-age=60, s-maxage=60"}
    sent.update(headers)
    return ok(commit_activity_payload() if body is None else body, **sent)


class Clock:
    def __init__(self, now: float = T0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Sleeps:
    """Records every wait the provider asks for. A commit-activity read must never ask for one."""

    def __init__(self) -> None:
        self.seconds: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.seconds.append(seconds)


def week(index: int, days: Sequence[int] | None = None, **overrides: Any) -> dict[str, Any]:
    counts = list(days if days is not None else [0, 1, 2, 3, 4, 5, 6])
    return {"days": counts, "total": sum(counts), "week": FIRST_FIXTURE_WEEK + index * 604_800, **overrides}


class TheWeeklyCounts(unittest.TestCase):
    def test_a_year_is_read_with_its_dates_its_days_and_its_totals(self) -> None:
        weeks = normalize_commit_activity(commit_activity_payload())
        self.assertEqual(len(weeks), 52)
        self.assertEqual(weeks[0].week_start, datetime(2025, 11, 9, tzinfo=UTC))
        self.assertEqual(weeks[0].week_start.weekday(), 6)  # a Sunday
        self.assertTrue(all(len(w.days) == 7 and w.total == sum(w.days) for w in weeks))
        quiet = [index for index, w in enumerate(weeks) if w.total == 0]
        self.assertEqual(quiet, [6, 19, 32, 45])  # a quiet week is kept as a week of zero, never dropped
        gaps = {(b.week_start - a.week_start).days for a, b in zip(weeks, weeks[1:], strict=False)}
        self.assertEqual(gaps, {7})

    def test_fields_nobody_asked_for_are_ignored_and_never_kept(self) -> None:
        (only,) = normalize_commit_activity([week(0, extra="https://evil.example/x")])
        self.assertNotIn("evil", repr(only))

    def test_an_empty_list_is_no_weeks_rather_than_an_error(self) -> None:
        self.assertEqual(normalize_commit_activity([]), ())

    def test_what_is_not_the_documented_shape_is_refused_not_repaired(self) -> None:
        good = week(0)
        cases: dict[str, Any] = {
            "an object, not a list": {"0": good},
            "a week that is not an object": [1],
            "six days": [week(0, [1, 2, 3, 4, 5, 6])],
            "eight days": [week(0, [1, 2, 3, 4, 5, 6, 7, 8])],
            "days that are not a list": [{**good, "days": "0000000"}],
            "a negative day": [week(0, [0, -1, 2, 0, 0, 0, 0], total=1)],
            "a day that is a boolean": [{**good, "days": [True, 0, 0, 0, 0, 0, 0], "total": 1}],
            "a day that is text": [{**good, "days": ["1", 0, 0, 0, 0, 0, 0], "total": 1}],
            "a day that is a float": [{**good, "days": [1.0, 0, 0, 0, 0, 0, 0], "total": 1}],
            "a total that is not the sum of its days": [{**good, "total": 99}],
            "a missing total": [{k: v for k, v in good.items() if k != "total"}],
            "a missing week": [{k: v for k, v in good.items() if k != "week"}],
            "a week that is a string": [{**good, "week": "1762646400"}],
            "a week that is a float": [{**good, "week": 1762646400.0}],
            "a week that is not a Sunday": [{**good, "week": FIRST_FIXTURE_WEEK + 86_400}],
            # GitHub's documentation example (1336280400) is five hours past midnight; the real answers were not.
            "a week that is not at midnight UTC": [{**good, "week": FIRST_FIXTURE_WEEK + 5 * 3_600}],
            "a week too large to be a date": [{**good, "week": 10**30}],
            "two weeks the same": [week(0), week(0)],
            "weeks out of order": [week(1), week(0)],
            "a week skipped": [week(0), week(2)],
            "more than a year of weeks": [week(i) for i in range(61)],
        }
        for name, payload in cases.items():
            with self.subTest(name):
                with self.assertRaises(MalformedGitHubPayloadError):
                    normalize_commit_activity(payload)

    def test_sixty_weeks_is_the_most_it_will_read(self) -> None:
        self.assertEqual(len(normalize_commit_activity([week(i) for i in range(60)])), 60)


class TheRead(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.sleeps = Sleeps()
        self.ref = RepoRef("fixture-org", "example-repo")

    def provider(self, script: Mapping[str, Any]) -> tuple[LiveGitHubProvider, FakeTransport]:
        transport = FakeTransport(script)
        provider = LiveGitHubProvider(
            transport, InMemoryTTLCache(), clock=self.clock, sleep=self.sleeps, jitter=lambda: 0.0
        )
        return provider, transport

    def script(self, stats: Any = None) -> dict[str, Any]:
        answer = stats if stats is not None else stats_ok()
        return {REPO: ok(repo_payload()), LANGS: ok(languages_payload()), STATS: answer}

    async def test_a_ready_year_is_read_after_the_repository_and_says_where_it_came_from(self) -> None:
        provider, transport = self.provider(self.script())
        result = await provider.commit_activity(self.ref)
        self.assertIs(result.status, CommitActivityStatus.READY)
        self.assertEqual(len(result.weeks), 52)
        self.assertEqual(result.full_name, "fixture-org/example-repo")
        self.assertIs(result.cache_status, CacheStatus.FRESH)
        self.assertEqual(transport.paths(), [REPO, STATS])
        self.assertEqual(result.source_urls, (f"https://api.github.com{REPO}", f"https://api.github.com{STATS}"))
        self.assertEqual(result.fetched_at, datetime.fromtimestamp(T0, UTC))

    async def test_a_202_is_computing_and_the_provider_does_not_wait_for_it(self) -> None:
        provider, transport = self.provider(self.script(COMPUTING))
        result = await provider.commit_activity(self.ref)
        self.assertIs(result.status, CommitActivityStatus.COMPUTING)
        self.assertEqual(result.weeks, ())
        self.assertIs(result.cache_status, CacheStatus.FRESH)
        self.assertEqual(transport.paths(), [REPO, STATS])  # asked once: no retry, no polling
        self.assertEqual(self.sleeps.seconds, [])  # and no waiting

    async def test_computing_is_not_kept_so_the_next_ask_goes_back_to_github(self) -> None:
        provider, transport = self.provider(self.script([COMPUTING, COMPUTING, stats_ok()]))
        first = await provider.commit_activity(self.ref)
        self.clock.now += 5
        second = await provider.commit_activity(self.ref)
        self.clock.now += 5
        third = await provider.commit_activity(self.ref)
        self.assertEqual([r.status.value for r in (first, second, third)], ["computing", "computing", "ready"])
        self.assertEqual(transport.paths().count(STATS), 3)  # every ask reached GitHub
        self.assertEqual(transport.paths().count(REPO), 1)  # the repository was reused for the first minute
        self.assertEqual(self.sleeps.seconds, [])
        self.assertEqual(len(third.weeks), 52)

    async def test_a_ready_year_is_reused_for_a_minute_then_confirmed_with_the_validators_it_came_with(self) -> None:
        stats_etag = 'W/"stats-1"'
        script = self.script([stats_ok(), NOT_MODIFIED])
        script[REPO] = [ok(repo_payload()), NOT_MODIFIED]
        provider, transport = self.provider(script)
        await provider.commit_activity(self.ref)
        self.clock.now += 30
        within = await provider.commit_activity(self.ref)
        self.assertEqual(transport.paths(), [REPO, STATS])  # nothing new was asked
        self.assertIs(within.cache_status, CacheStatus.FRESH)
        self.clock.now += 90
        again = await provider.commit_activity(self.ref)
        self.assertIs(again.cache_status, CacheStatus.REVALIDATED)
        self.assertEqual(len(again.weeks), 52)  # the stored body is served on a 304
        self.assertEqual(transport.calls[-1], (STATS, {"if-none-match": stats_etag}))

    async def test_a_computing_answer_beside_a_confirmed_repository_is_live_not_confirmed(self) -> None:
        script = self.script(COMPUTING)
        script[REPO] = [ok(repo_payload()), NOT_MODIFIED]
        provider, _ = self.provider(script)
        await provider.analyze_repository(self.ref)
        self.clock.now += 120
        result = await provider.commit_activity(self.ref)
        self.assertIs(result.status, CommitActivityStatus.COMPUTING)
        self.assertIs(result.cache_status, CacheStatus.FRESH)  # a 202 was read just now; nothing was "unchanged"

    async def test_the_time_is_the_oldest_of_the_reads_so_the_year_is_never_newer_than_its_parts(self) -> None:
        provider, _ = self.provider(self.script(stats_ok()))
        await provider.analyze_repository(self.ref)  # the repository is read at T0
        self.clock.now += 30
        result = await provider.commit_activity(self.ref)  # the year is read at T0 + 30
        self.assertIs(result.cache_status, CacheStatus.FRESH)
        self.assertEqual(result.fetched_at, datetime.fromtimestamp(T0, UTC))

    async def test_a_confirmed_repository_beside_a_year_served_from_the_copy_is_confirmed(self) -> None:
        script = self.script(stats_ok())
        script[REPO] = [ok(repo_payload()), NOT_MODIFIED]
        provider, transport = self.provider(script)
        await provider.analyze_repository(self.ref)
        self.clock.now += 30
        await provider.commit_activity(self.ref)  # the year is stored at T0 + 30
        self.clock.now += 40  # the repository copy is now 70 seconds old, the year's only 40
        result = await provider.commit_activity(self.ref)
        self.assertEqual(transport.paths(), [REPO, LANGS, STATS, REPO])  # the year was not asked again
        self.assertIs(result.cache_status, CacheStatus.REVALIDATED)  # every part came from a copy, one just confirmed
        self.assertEqual(result.fetched_at, datetime.fromtimestamp(T0 + 30, UTC))

    async def test_a_year_that_github_is_recomputing_is_computing_not_a_stale_copy(self) -> None:
        provider, _ = self.provider(self.script([stats_ok(), COMPUTING]))
        await provider.commit_activity(self.ref)
        self.clock.now += 120  # past the freshness window, so GitHub is asked again and says it is working
        result = await provider.commit_activity(self.ref)
        self.assertIs(result.status, CommitActivityStatus.COMPUTING)
        self.assertEqual(result.weeks, ())

    async def test_when_github_cannot_be_reached_the_last_year_is_shown_as_stale(self) -> None:
        provider, _ = self.provider(self.script([stats_ok(), GitHubUnavailableError("down")]))
        await provider.commit_activity(self.ref)
        self.clock.now += 120
        result = await provider.commit_activity(self.ref)
        self.assertIs(result.status, CommitActivityStatus.READY)
        self.assertIs(result.cache_status, CacheStatus.STALE)
        self.assertEqual(result.fetched_at, datetime.fromtimestamp(T0, UTC))  # never newer than the copy

    async def test_a_204_is_none_and_is_not_kept(self) -> None:
        provider, transport = self.provider(self.script([NO_CONTENT, stats_ok()]))
        first = await provider.commit_activity(self.ref)
        self.assertIs(first.status, CommitActivityStatus.NONE)
        self.assertEqual(first.weeks, ())
        self.assertIs(first.cache_status, CacheStatus.FRESH)
        self.clock.now += 5
        later = await provider.commit_activity(self.ref)
        self.assertIs(later.status, CommitActivityStatus.READY)  # the empty answer was not turned into a stored one
        self.assertEqual(transport.paths().count(STATS), 2)

    async def test_an_empty_list_is_none_too(self) -> None:
        provider, _ = self.provider(self.script(stats_ok([])))
        result = await provider.commit_activity(self.ref)
        self.assertEqual((result.status, result.weeks), (CommitActivityStatus.NONE, ()))

    async def test_an_answer_that_is_not_the_documented_shape_is_an_error_not_a_chart(self) -> None:
        for body in ({"weeks": []}, [{"days": [1], "total": 1, "week": FIRST_FIXTURE_WEEK}], "text"):
            with self.subTest(body=repr(body)[:40]):
                provider, _ = self.provider(self.script(stats_ok(body)))
                with self.assertRaises(MalformedGitHubPayloadError):
                    await provider.commit_activity(self.ref)

    async def test_other_failures_propagate_as_they_do_for_every_other_read(self) -> None:
        cases = (
            (error(404, "Not Found"), GitHubNotFoundError),
            (error(403, "Resource not accessible"), GitHubForbiddenError),
            (RawResponse(500, {}, b"{}"), GitHubUnavailableError),
        )
        for response, expected in cases:
            with self.subTest(status=response.status):
                provider, _ = self.provider(self.script(response))
                with self.assertRaises(expected):
                    await provider.commit_activity(self.ref)

    async def test_a_rate_limit_is_reported_as_one_and_not_slept_through(self) -> None:
        limited = error(
            403, "API rate limit exceeded", **{"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(T0) + 3000)}
        )
        provider, _ = self.provider(self.script(limited))
        with self.assertRaises(GitHubRateLimitedError):
            await provider.commit_activity(self.ref)
        self.assertEqual(self.sleeps.seconds, [])

    async def test_a_private_repository_is_not_found_and_commit_activity_is_never_asked(self) -> None:
        script = self.script()
        script[REPO] = ok(repo_payload(private=True))
        provider, transport = self.provider(script)
        with self.assertRaises(GitHubNotFoundError):
            await provider.commit_activity(self.ref)
        self.assertEqual(transport.paths(), [REPO])

    async def test_a_repository_whose_name_is_spelled_differently_by_github_is_refused(self) -> None:
        script = self.script()
        script[REPO] = ok(repo_payload(full_name="someone-else/example-repo"))
        provider, transport = self.provider(script)
        with self.assertRaises(MalformedGitHubPayloadError):
            await provider.commit_activity(self.ref)
        self.assertEqual(transport.paths(), [REPO])

    async def test_asking_for_it_does_not_change_what_the_basic_analysis_reads(self) -> None:
        provider, transport = self.provider(self.script())
        await provider.analyze_repository(self.ref)
        self.assertEqual(transport.paths(), [REPO, LANGS])  # commit activity is opt-in: never part of the analysis



class TheRoute(unittest.TestCase):
    def live_app(self, script: dict[str, Any]) -> Any:
        async def no_sleep(seconds: float) -> None:
            return None

        provider = LiveGitHubProvider(
            FakeTransport(script), InMemoryTTLCache(), clock=Clock(), sleep=no_sleep, jitter=lambda: 0.0
        )
        return make_app(github=provider)

    def script(self, stats: Any) -> dict[str, Any]:
        return {REPO: ok(repo_payload()), STATS: stats}

    def test_a_ready_year_matches_its_schema_and_says_where_it_came_from(self) -> None:
        response, body = get(self.live_app(self.script(stats_ok())), URL)
        self.assertEqual(response.status, 200, body)
        self.assertEqual(validate(body, COMPONENTS["CommitActivity"], COMPONENTS), [])
        self.assertEqual((body["status"], body["provenance"], len(body["weeks"])), ("ready", "sourced", 52))
        self.assertEqual(body["html_url"], "https://github.com/fixture-org/example-repo")
        self.assertEqual(len(body["source_urls"]), 2)
        self.assertEqual(set(body["weeks"][0]), {"week_start", "days", "total"})
        self.assertEqual(body["weeks"][0]["week_start"], "2025-11-09T00:00:00Z")

    def test_computing_is_a_200_with_no_weeks_and_never_a_made_up_chart(self) -> None:
        response, body = get(self.live_app(self.script(COMPUTING)), URL)
        self.assertEqual(response.status, 200, body)
        self.assertEqual(validate(body, COMPONENTS["CommitActivity"], COMPONENTS), [])
        self.assertEqual((body["status"], body["weeks"]), ("computing", []))
        self.assertEqual(body["provenance"], "sourced")  # the 202 itself is what was read, and its URL is listed

    def test_no_activity_is_stated_as_none(self) -> None:
        response, body = get(self.live_app(self.script(NO_CONTENT)), URL)
        self.assertEqual((response.status, body["status"], body["weeks"]), (200, "none", []))

    def test_the_fixture_provider_answers_in_the_same_schema_in_every_state(self) -> None:
        for status in CommitActivityStatus:
            with self.subTest(status=status.value):
                response, body = get(make_app(github=FakeGitHubProvider(commit_status=status)), URL)
                self.assertEqual(response.status, 200, body)
                self.assertEqual(validate(body, COMPONENTS["CommitActivity"], COMPONENTS), [])
                self.assertEqual(len(body["weeks"]), 52 if status is CommitActivityStatus.READY else 0)

    def test_the_answer_is_never_cached_by_a_browser_or_a_proxy(self) -> None:
        for stats in (stats_ok(), COMPUTING):
            with self.subTest(status=stats.status):
                response, _ = get(self.live_app(self.script(stats)), URL)
                self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_names_that_are_not_repositories_never_reach_the_provider(self) -> None:
        provider = FakeGitHubProvider()
        app = make_app(github=provider)
        for owner, repo in (("github.com", "vercel"), ("-bad", "repo"), ("owner", ".."), ("owner", "a b")):
            with self.subTest(owner=owner, repo=repo):
                response, body = get(app, f"/api/v1/github/repositories/{owner}/{repo}/commit-activity")
                self.assertIn(response.status, (400, 404, 422))
                self.assertIsNotNone(body)
        self.assertEqual(provider.requested, [])

    def test_with_github_switched_off_it_says_so(self) -> None:
        response, body = get(make_app(github=None), URL)
        self.assertEqual((response.status, body["error"]["code"]), (503, "service_unavailable"))

    def test_failures_use_the_same_fixed_messages_as_every_other_github_route(self) -> None:
        cases = (
            (GitHubNotFoundError("secret detail"), 404, "not_found"),
            (GitHubUnavailableError("secret detail"), 502, "upstream_error"),
            (GitHubRateLimitedError("secret detail", 90.0), 503, "upstream_rate_limited"),
            (MalformedGitHubPayloadError("secret detail"), 502, "upstream_error"),
        )
        for exc, status, code in cases:
            with self.subTest(exc=type(exc).__name__):
                app = make_app(github=FakeGitHubProvider(fail_with=exc))
                with self.assertLogs("vyron.api", level="INFO"):
                    response, body = get(app, URL)
                self.assertEqual((response.status, body["error"]["code"]), (status, code))
                self.assertNotIn("secret detail", json.dumps(body))


if __name__ == "__main__":
    unittest.main()
