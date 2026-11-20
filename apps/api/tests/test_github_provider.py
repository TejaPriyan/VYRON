"""The live provider against a scripted transport. No network; the clock and sleep are injected.

The payloads are fixtures written from GitHub's documentation (see github_fakes.py), so these tests show that
VYRON behaves correctly *given* those shapes, not that GitHub sends them.
"""

import unittest
from datetime import UTC, datetime

from github_fakes import (
    FIXTURE_REF,
    FakeTransport,
    error,
    languages_payload,
    ok,
    rate_limit_payload,
    repo_payload,
)
from vyron_api.adapters.memory_cache import InMemoryTTLCache
from vyron_api.github.budget import RateLimitBudget
from vyron_api.github.normalize import MalformedGitHubPayloadError
from vyron_api.github.provider import LiveGitHubProvider, build_live_provider
from vyron_api.github.retry import RawResponse
from vyron_api.github.url import RepoRef
from vyron_api.providers.github import (
    CacheStatus,
    GitHubError,
    GitHubForbiddenError,
    GitHubMovedError,
    GitHubNotFoundError,
    GitHubRateLimitedError,
    GitHubUnavailableError,
)

T0 = 1_792_577_730.0  # 2026-10-21T10:15:30Z
REPO = FIXTURE_REF.api_path
LANGS = f"{REPO}/languages"
RESET = int(T0) + 2_000


class Clock:
    def __init__(self, now: float = T0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class ProviderCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def provider(self, transport: FakeTransport, **kwargs: object) -> LiveGitHubProvider:
        return LiveGitHubProvider(
            transport,
            InMemoryTTLCache(),
            clock=self.clock,
            sleep=self.sleep,
            jitter=lambda: 0.0,
            **kwargs,  # type: ignore[arg-type]
        )

    def healthy(self, **repo_overrides: object) -> FakeTransport:
        return FakeTransport({REPO: ok(repo_payload(**repo_overrides)), LANGS: ok(languages_payload())})


class AnalysisTest(ProviderCase):
    async def test_reads_both_resources_and_says_where_and_when(self) -> None:
        transport = self.healthy()
        result = await self.provider(transport).analyze_repository(FIXTURE_REF)
        snapshot = result.snapshot
        self.assertEqual(snapshot.full_name, "fixture-org/example-repo")
        self.assertEqual((snapshot.stars, snapshot.forks, snapshot.open_issues), (1234, 56, 7))
        self.assertEqual(snapshot.license_spdx, "MIT")
        self.assertEqual(list(snapshot.topics), ["fixture", "example"])
        self.assertEqual(dict(result.languages), languages_payload())
        self.assertEqual(result.cache_status, CacheStatus.FRESH)
        self.assertEqual(result.fetched_at, datetime.fromtimestamp(T0, UTC))
        self.assertEqual(
            list(result.source_urls),
            [f"https://api.github.com{REPO}", f"https://api.github.com{LANGS}"],
        )
        self.assertEqual(transport.paths(), [REPO, LANGS])

    async def test_a_copy_confirmed_within_the_minute_is_served_without_asking_github(self) -> None:
        transport = self.healthy()
        provider = self.provider(transport)
        await provider.analyze_repository(FIXTURE_REF)
        self.clock.now = T0 + 30
        again = await provider.analyze_repository(FIXTURE_REF)
        self.assertEqual(len(transport.calls), 2)  # nothing new was requested
        self.assertEqual(again.cache_status, CacheStatus.FRESH)
        self.assertEqual(again.fetched_at, datetime.fromtimestamp(T0, UTC))  # when GitHub last said so, not now

    async def test_an_older_copy_is_revalidated_and_reported_as_such(self) -> None:
        not_modified = RawResponse(304, {"x-ratelimit-remaining": "57"}, b"")
        transport = FakeTransport(
            {REPO: [ok(repo_payload()), not_modified], LANGS: [ok(languages_payload()), not_modified]}
        )
        provider = self.provider(transport)
        await provider.analyze_repository(FIXTURE_REF)
        self.clock.now = T0 + 120
        again = await provider.analyze_repository(FIXTURE_REF)
        self.assertEqual(again.cache_status, CacheStatus.REVALIDATED)
        self.assertEqual(again.fetched_at, datetime.fromtimestamp(T0 + 120, UTC))
        self.assertEqual(transport.calls[2][1], {"if-none-match": '"fixture-etag"'})
        self.assertEqual(again.snapshot.stars, 1234)

    async def test_a_changed_repository_replaces_the_copy_and_is_a_fresh_read(self) -> None:
        newer = ok(repo_payload(stargazers_count=1300), etag='"v2"')
        transport = FakeTransport({REPO: [ok(repo_payload()), newer], LANGS: ok(languages_payload())})
        provider = self.provider(transport)
        await provider.analyze_repository(FIXTURE_REF)
        self.clock.now = T0 + 120
        again = await provider.analyze_repository(FIXTURE_REF)
        self.assertEqual(again.snapshot.stars, 1300)
        self.assertEqual(again.cache_status, CacheStatus.FRESH)  # two 200s: nothing was merely confirmed
        self.assertEqual(again.fetched_at, datetime.fromtimestamp(T0 + 120, UTC))

    async def test_one_changed_and_one_confirmed_resource_is_fresh_not_nothing_changed(self) -> None:
        newer = ok(repo_payload(stargazers_count=1300), etag='"v2"')
        not_modified = RawResponse(304, {"x-ratelimit-remaining": "57"}, b"")
        transport = FakeTransport({REPO: [ok(repo_payload()), newer], LANGS: [ok(languages_payload()), not_modified]})
        provider = self.provider(transport)
        await provider.analyze_repository(FIXTURE_REF)
        self.clock.now = T0 + 120
        again = await provider.analyze_repository(FIXTURE_REF)
        self.assertEqual(again.snapshot.stars, 1300)
        self.assertEqual(dict(again.languages), languages_payload())
        # "Confirmed: nothing had changed" would be false: the stars did. A part read now makes it fresh.
        self.assertEqual(again.cache_status, CacheStatus.FRESH)

    async def test_an_older_copy_is_offered_as_stale_when_github_cannot_be_reached(self) -> None:
        transport = FakeTransport(
            {REPO: [ok(repo_payload()), OSError("down")], LANGS: [ok(languages_payload()), OSError("down")]}
        )
        provider = self.provider(transport)
        await provider.analyze_repository(FIXTURE_REF)
        self.clock.now = T0 + 120
        again = await provider.analyze_repository(FIXTURE_REF)
        self.assertEqual(again.cache_status, CacheStatus.STALE)
        self.assertEqual(again.fetched_at, datetime.fromtimestamp(T0, UTC))  # honest about its age
        self.assertEqual(again.snapshot.stars, 1234)

    async def test_without_any_copy_an_unreachable_github_is_an_error(self) -> None:
        transport = FakeTransport({REPO: OSError("down")})
        with self.assertRaises(GitHubUnavailableError):
            await self.provider(transport).analyze_repository(FIXTURE_REF)

    async def test_names_are_matched_without_regard_to_case_and_reported_as_github_spells_them(self) -> None:
        ref = RepoRef("Fixture-Org", "Example-Repo")
        transport = FakeTransport({ref.api_path: ok(repo_payload()), f"{ref.api_path}/languages": ok({})})
        result = await self.provider(transport).analyze_repository(ref)
        self.assertEqual(result.snapshot.full_name, "fixture-org/example-repo")

    async def test_a_repository_with_no_languages_is_an_empty_breakdown_not_an_error(self) -> None:
        transport = FakeTransport({REPO: ok(repo_payload(language=None)), LANGS: ok({})})
        result = await self.provider(transport).analyze_repository(FIXTURE_REF)
        self.assertEqual(dict(result.languages), {})
        self.assertIsNone(result.snapshot.primary_language)

    async def test_the_two_single_resource_reads_work_alone(self) -> None:
        transport = self.healthy()
        provider = self.provider(transport)
        self.assertEqual((await provider.get_repository(FIXTURE_REF)).stars, 1234)
        self.assertEqual(dict(await provider.get_languages(FIXTURE_REF)), languages_payload())


class RefusalTest(ProviderCase):
    async def test_a_missing_repository_stops_before_the_second_request(self) -> None:
        transport = FakeTransport({REPO: error(404, "Not Found")})
        with self.assertRaises(GitHubNotFoundError):
            await self.provider(transport).analyze_repository(FIXTURE_REF)
        self.assertEqual(transport.paths(), [REPO])

    async def test_a_private_repository_is_reported_as_not_found(self) -> None:
        for overrides in ({"private": True}, {"private": None}, {"private": "false"}):
            with self.subTest(overrides=overrides):
                with self.assertRaises(GitHubNotFoundError):
                    await self.provider(self.healthy(**overrides)).analyze_repository(FIXTURE_REF)

    async def test_a_response_without_the_private_field_is_refused(self) -> None:
        payload = repo_payload()
        del payload["private"]
        with self.assertRaises(GitHubNotFoundError):
            await self.provider(FakeTransport({REPO: ok(payload)})).analyze_repository(FIXTURE_REF)

    async def test_a_cached_private_body_is_refused_every_time(self) -> None:
        transport = self.healthy(private=True)
        provider = self.provider(transport)
        for _ in range(2):
            with self.assertRaises(GitHubNotFoundError):
                await provider.analyze_repository(FIXTURE_REF)
        self.assertEqual(transport.paths(), [REPO])  # the second refusal came from the cached copy, re-checked

    async def test_an_answer_about_a_different_repository_is_refused(self) -> None:
        transport = self.healthy(full_name="other-org/other-repo")
        with self.assertRaises(MalformedGitHubPayloadError):
            await self.provider(transport).analyze_repository(FIXTURE_REF)

    async def test_a_name_that_is_not_a_valid_repository_name_is_refused(self) -> None:
        transport = self.healthy(full_name="not a name")
        with self.assertRaises(MalformedGitHubPayloadError):
            await self.provider(transport).analyze_repository(FIXTURE_REF)

    async def test_a_renamed_repository_is_reported_not_followed(self) -> None:
        moved = error(301, "Moved Permanently", location="https://api.github.com/repositories/1296269")
        transport = FakeTransport({REPO: moved})
        with self.assertRaises(GitHubMovedError):
            await self.provider(transport).analyze_repository(FIXTURE_REF)
        self.assertEqual(transport.paths(), [REPO])  # the Location was not requested

    async def test_a_plain_403_is_forbidden_and_is_not_retried(self) -> None:
        gateway = error(403, "No linked GitHub account. Connect your GitHub account and retry.")
        transport = FakeTransport({REPO: gateway})
        with self.assertRaises(GitHubForbiddenError):
            await self.provider(transport).analyze_repository(FIXTURE_REF)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(self.sleeps, [])

    async def test_a_long_rate_limit_wait_is_reported_not_slept_through(self) -> None:
        limited = error(
            403, "API rate limit exceeded",
            **{"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(T0) + 500)},
        )  # fmt: skip
        transport = FakeTransport({REPO: limited})
        with self.assertRaises(GitHubRateLimitedError) as caught:
            await self.provider(transport).analyze_repository(FIXTURE_REF)
        self.assertAlmostEqual(caught.exception.wait_seconds, 501.0)
        self.assertEqual(self.sleeps, [])
        self.assertEqual(len(transport.calls), 1)

    async def test_a_transient_failure_is_retried_once_after_a_short_wait(self) -> None:
        transport = FakeTransport({REPO: [error(503, "unavailable"), ok(repo_payload())], LANGS: ok({})})
        result = await self.provider(transport).analyze_repository(FIXTURE_REF)
        self.assertEqual(result.snapshot.stars, 1234)
        self.assertEqual(self.sleeps, [0.5])  # interactive: never minutes

    async def test_a_failing_second_resource_fails_the_whole_analysis(self) -> None:
        transport = FakeTransport({REPO: ok(repo_payload()), LANGS: error(500, "boom")})
        with self.assertRaises(GitHubUnavailableError):
            await self.provider(transport).analyze_repository(FIXTURE_REF)

    async def test_malformed_responses_are_refused_not_repaired(self) -> None:
        cases = {
            "not json": (RawResponse(200, {"etag": '"x"'}, b"<html>"), GitHubError),
            "a list": (ok([1, 2, 3]), GitHubNotFoundError),  # no `private: false`, so not accepted as public
            "stars": (ok(repo_payload(stargazers_count="many")), MalformedGitHubPayloadError),
            "negative": (ok(repo_payload(forks_count=-1)), MalformedGitHubPayloadError),
            "topics": (ok(repo_payload(topics="fixture")), MalformedGitHubPayloadError),
            "timestamp": (ok(repo_payload(pushed_at="yesterday")), MalformedGitHubPayloadError),
        }
        for name, (response, expected) in cases.items():
            with self.subTest(name):
                with self.assertRaises(expected):
                    await self.provider(FakeTransport({REPO: response})).analyze_repository(FIXTURE_REF)

    async def test_a_malformed_language_breakdown_is_refused(self) -> None:
        for body in ([["Python", 1]], {"Python": "lots"}, {"Python": -5}):
            with self.subTest(body=body):
                transport = FakeTransport({REPO: ok(repo_payload()), LANGS: ok(body)})
                with self.assertRaises(MalformedGitHubPayloadError):
                    await self.provider(transport).analyze_repository(FIXTURE_REF)


class BudgetTest(ProviderCase):
    def low_quota(self) -> FakeTransport:
        headers = {"x-ratelimit-remaining": "5", "x-ratelimit-reset": str(RESET)}
        return FakeTransport({REPO: ok(repo_payload(), **headers), LANGS: ok(languages_payload(), **headers)})

    async def test_a_nearly_spent_quota_refuses_the_next_request(self) -> None:
        transport = self.low_quota()
        provider = self.provider(transport, budget=RateLimitBudget(reserve=10, now=self.clock))
        with self.assertRaises(GitHubRateLimitedError) as caught:
            await provider.analyze_repository(FIXTURE_REF)
        self.assertGreater(caught.exception.wait_seconds, 1000)
        self.assertEqual(transport.paths(), [REPO])  # the second request was never sent

    async def test_an_older_copy_is_offered_instead_when_the_quota_is_spent(self) -> None:
        transport = self.healthy()
        provider = self.provider(transport, budget=RateLimitBudget(reserve=10, now=self.clock))
        await provider.analyze_repository(FIXTURE_REF)  # fills the cache; the quota still looks healthy
        provider._budget.update(
            {"x-ratelimit-limit": "60", "x-ratelimit-remaining": "3", "x-ratelimit-reset": str(RESET)}
        )
        self.clock.now = T0 + 120
        again = await provider.analyze_repository(FIXTURE_REF)
        self.assertEqual(again.cache_status, CacheStatus.STALE)
        self.assertEqual(len(transport.calls), 2)


class RateLimitStatusTest(ProviderCase):
    async def test_the_quota_comes_from_the_last_response_without_another_request(self) -> None:
        transport = self.healthy()
        provider = self.provider(transport)
        await provider.analyze_repository(FIXTURE_REF)
        state = await provider.rate_limit_status()
        self.assertEqual((state.limit, state.remaining, state.resource), (60, 59, "core"))
        self.assertEqual(transport.paths(), [REPO, LANGS])

    async def test_without_a_response_yet_github_is_asked_and_the_answer_is_checked(self) -> None:
        transport = FakeTransport({"/rate_limit": ok(rate_limit_payload(limit=5000, remaining=4990))})
        state = await self.provider(transport).rate_limit_status()
        self.assertEqual((state.limit, state.remaining, state.used, state.resource), (5000, 4990, 10, "core"))
        self.assertEqual(state.reset_epoch, 1_792_580_000)
        self.assertEqual(transport.paths(), ["/rate_limit"])

    async def test_a_window_that_has_reset_is_asked_again(self) -> None:
        closing = {"x-ratelimit-reset": str(int(T0) + 100)}  # on BOTH responses: the budget keeps the last one seen
        transport = FakeTransport(
            {
                REPO: ok(repo_payload(), **closing),
                LANGS: ok({}, **closing),
                "/rate_limit": ok(rate_limit_payload(limit=60, remaining=60, reset=int(T0) + 4000)),
            }
        )
        provider = self.provider(transport)
        await provider.analyze_repository(FIXTURE_REF)
        self.clock.now = T0 + 200  # past the reset the last response announced
        state = await provider.rate_limit_status()
        self.assertEqual(state.remaining, 60)
        self.assertEqual(transport.paths()[-1], "/rate_limit")

    async def test_a_malformed_quota_answer_is_refused(self) -> None:
        bad_bodies: tuple[object, ...] = (
            [],
            {"resources": []},
            {"resources": {}},
            {"resources": {"core": {"limit": "x"}}},
        )
        for body in bad_bodies:
            with self.subTest(body=body):
                transport = FakeTransport({"/rate_limit": ok(body)})
                with self.assertRaises(MalformedGitHubPayloadError):
                    await self.provider(transport).rate_limit_status()

    async def test_a_refused_quota_request_is_an_error_not_a_guess(self) -> None:
        transport = FakeTransport({"/rate_limit": error(403, "No linked GitHub account")})
        with self.assertRaises(GitHubForbiddenError):
            await self.provider(transport).rate_limit_status()


class FactoryTest(unittest.TestCase):
    def test_the_real_host_is_the_default_and_nothing_is_requested_when_building(self) -> None:
        provider = build_live_provider()
        self.assertIsInstance(provider, LiveGitHubProvider)

    def test_another_host_is_refused_unless_a_loopback_test_server_is_allowed(self) -> None:
        for url in ("https://example.com", "http://api.github.com", "http://127.0.0.1:9"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                build_live_provider(base_url=url)
        build_live_provider(base_url="http://127.0.0.1:9", allow_local_test_server=True)
        with self.assertRaises(ValueError):
            build_live_provider(base_url="http://example.com", allow_local_test_server=True)


if __name__ == "__main__":
    unittest.main()
