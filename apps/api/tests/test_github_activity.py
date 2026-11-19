"""Slice 2 of repository analysis: top contributors and the latest release (the opt-in read).

EVERY PAYLOAD HERE WAS WRITTEN FROM GITHUB'S DOCUMENTATION (see github_fakes.py). None was captured from GitHub, so
these tests show that VYRON behaves correctly *given* those shapes, not that GitHub sends them. The first real
captures are to come from ``scripts/live_github_check.py --capture``; until they are in
``fixtures/github_real`` this slice is unverified against GitHub.
"""

import json
import unittest
from typing import Any

from github_fakes import (
    FIXTURE_REF,
    FakeGitHubProvider,
    FakeTransport,
    contributors_payload,
    error,
    languages_payload,
    ok,
    release_payload,
    repo_payload,
)
from test_api_app import get, make_app
from vyron_api.adapters.memory_cache import InMemoryTTLCache
from vyron_api.api.schemas import COMPONENTS
from vyron_api.api.validate import validate
from vyron_api.github.normalize import (
    MalformedGitHubPayloadError,
    normalize_contributors,
    normalize_release,
)
from vyron_api.github.provider import LiveGitHubProvider
from vyron_api.github.retry import RawResponse
from vyron_api.github.url import RepoRef
from vyron_api.providers.github import (
    CacheStatus,
    ContributorsStatus,
    GitHubError,
    GitHubNotFoundError,
    GitHubRateLimitedError,
    GitHubUnavailableError,
)

T0 = 1_792_577_730.0  # 2026-10-21T10:15:30Z
REPO = FIXTURE_REF.api_path
LANGS = f"{REPO}/languages"
CONTRIBUTORS = f"{REPO}/contributors?per_page=10"
RELEASE = f"{REPO}/releases/latest"
ACTIVITY_URL = f"/api/v1/github/repositories/{FIXTURE_REF.owner}/{FIXTURE_REF.repo}/activity"
# A 204 that carries a validator, so the cache would keep it if it were allowed to.
NO_CONTENT = RawResponse(204, {"etag": '"empty"', "x-ratelimit-remaining": "50"}, b"")
NOT_MODIFIED = RawResponse(304, {"x-ratelimit-remaining": "40"}, b"")


def person(login: str, contributions: int, kind: str = "User", **extra: Any) -> dict[str, Any]:
    return {"login": login, "id": 1, "type": kind, "contributions": contributions, **extra}


class Clock:
    def __init__(self, now: float = T0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class TheContributorList(unittest.TestCase):
    def test_accounts_are_read_with_the_four_values_and_a_link_built_from_the_login(self) -> None:
        result = normalize_contributors(contributors_payload())
        self.assertEqual([c.login for c in result], ["fixture-person-a", "fixture-person-b", "fixture-helper[bot]"])
        self.assertEqual([c.contributions for c in result], [120, 45, 30])
        self.assertEqual([c.is_bot for c in result], [False, False, True])
        self.assertEqual(result[0].profile_url, "https://github.com/fixture-person-a")
        self.assertIsNone(result[2].profile_url)  # a bot has no profile page at github.com/<login>

    def test_a_bot_is_never_linked_even_when_its_login_looks_like_an_ordinary_account(self) -> None:
        # Bot pages are not at github.com/<login>, so a link built from the login would point at someone else.
        (only,) = normalize_contributors([person("plain-name", 3, kind="Bot")])
        self.assertEqual((only.is_bot, only.profile_url), (True, None))

    def test_the_payloads_own_urls_are_never_copied(self) -> None:
        hostile = person("fixture-a", 5, html_url="https://evil.example/x", avatar_url="https://evil.example/a.png")
        (only,) = normalize_contributors([hostile])
        self.assertEqual(only.profile_url, "https://github.com/fixture-a")
        self.assertNotIn("evil", repr(only))

    def test_the_list_is_ordered_by_contributions_and_ties_keep_githubs_order(self) -> None:
        payload = [person("low", 1), person("tie-first", 9), person("high", 50), person("tie-second", 9)]
        self.assertEqual(
            [c.login for c in normalize_contributors(payload)], ["high", "tie-first", "tie-second", "low"]
        )

    def test_it_is_cut_to_the_limit(self) -> None:
        payload = [person(f"user{i}", 100 - i) for i in range(30)]
        self.assertEqual(len(normalize_contributors(payload)), 10)
        self.assertEqual(len(normalize_contributors(payload, limit=3)), 3)

    def test_a_login_with_an_underscore_is_shown_but_not_linked(self) -> None:
        (only,) = normalize_contributors([person("name_corp", 3)])  # enterprise-managed accounts look like this
        self.assertEqual((only.login, only.profile_url), ("name_corp", None))

    def test_an_anonymous_entry_is_dropped_and_its_email_never_kept(self) -> None:
        anonymous = {"type": "Anonymous", "email": "someone@example.invalid", "name": "Someone", "contributions": 7}
        result = normalize_contributors([anonymous, person("fixture-a", 5)])
        self.assertEqual([c.login for c in result], ["fixture-a"])
        self.assertNotIn("example.invalid", repr(result))

    def test_an_empty_list_is_an_empty_tuple(self) -> None:
        self.assertEqual(normalize_contributors([]), ())

    def test_shapes_that_are_not_what_github_documents_are_refused_not_guessed(self) -> None:
        bad: list[Any] = [
            {},  # an object where a list belongs
            "x",
            [person("ok", 1), "not an object"],
            [{"login": "a", "contributions": 1}],  # no type
            [person("a", 1, kind=None)],
            [{"type": "User", "contributions": 1}],  # no login
            [person(7, 1)],
            [person("", 1)],
            [person("has space", 1)],
            [person("-leading", 1)],
            [person("x" * 60, 1)],
            [person("a</b>", 1)],
            [person("a", -1)],
            [person("a", True)],
            [person("a", "5")],
            [{"login": "a", "type": "User"}],  # no contributions
            [person(f"user{i}", 1) for i in range(101)],  # more than a page can hold
        ]
        for payload in bad:
            with self.subTest(payload=str(payload)[:60]), self.assertRaises(MalformedGitHubPayloadError):
                normalize_contributors(payload)


class TheLatestRelease(unittest.TestCase):
    def test_a_release_is_read_without_its_notes_assets_or_author(self) -> None:
        release = normalize_release(release_payload(), FIXTURE_REF)
        self.assertEqual((release.tag_name, release.name, release.prerelease), ("v1.2.3", "Fixture release", False))
        self.assertEqual(release.published_at.isoformat(), "2026-09-01T12:00:00+00:00")  # type: ignore[union-attr]
        self.assertEqual(release.html_url, "https://github.com/fixture-org/example-repo/releases/tag/v1.2.3")
        self.assertNotIn("notes", repr(release))

    def test_a_release_with_no_name_and_no_publish_time_is_allowed(self) -> None:
        release = normalize_release(release_payload(name=None, published_at=None), FIXTURE_REF)
        self.assertEqual((release.name, release.published_at), (None, None))

    def test_the_link_must_be_this_repositorys_releases_page_on_github(self) -> None:
        fallback = "https://github.com/fixture-org/example-repo/releases"
        for url in (
            "https://evil.example/fixture-org/example-repo/releases/tag/v1",
            "https://github.com/other-org/other-repo/releases/tag/v1",
            "https://github.com.evil.example/fixture-org/example-repo/releases/tag/v1",
            "http://github.com/fixture-org/example-repo/releases/tag/v1",
            "https://github.com/fixture-org/example-repo/issues/1",
            "https://github.com/fixture-org/example-repo/releases/tag/v1 onclick=x",
            "https://github.com/fixture-org/example-repo/releases/tag/<script>",
            "javascript:alert(1)",
            "",
            None,
            7,
        ):
            with self.subTest(url=url):
                self.assertEqual(normalize_release(release_payload(html_url=url), FIXTURE_REF).html_url, fallback)

    def test_a_link_to_a_tag_with_a_slash_or_plus_is_kept(self) -> None:
        url = "https://github.com/fixture-org/example-repo/releases/tag/release/1.0.0%2Bbuild"
        self.assertEqual(normalize_release(release_payload(html_url=url), FIXTURE_REF).html_url, url)

    def test_the_link_is_compared_to_githubs_spelling_of_the_name_without_regard_to_case(self) -> None:
        url = "https://github.com/Fixture-Org/Example-Repo/releases/tag/v1"
        self.assertEqual(normalize_release(release_payload(html_url=url), FIXTURE_REF).html_url, url)

    def test_a_draft_is_refused_because_latest_is_never_a_draft(self) -> None:
        with self.assertRaises(MalformedGitHubPayloadError):
            normalize_release(release_payload(draft=True), FIXTURE_REF)

    def test_control_characters_never_survive(self) -> None:
        with self.assertRaises(MalformedGitHubPayloadError):
            normalize_release(release_payload(tag_name="v1\n<b>"), FIXTURE_REF)
        release = normalize_release(release_payload(name="Fix\x00ture\nrelease"), FIXTURE_REF)
        self.assertEqual(release.name, "Fix ture release")

    def test_wrong_shapes_are_refused(self) -> None:
        for payload in (
            [],
            "x",
            release_payload(tag_name=""),
            release_payload(tag_name=None),
            release_payload(tag_name="x" * 300),
            release_payload(name=5),
            release_payload(prerelease="no"),
            release_payload(published_at="yesterday"),
            release_payload(published_at="2026-09-01T12:00:00"),  # no timezone
            {k: v for k, v in release_payload().items() if k != "draft"},
            {k: v for k, v in release_payload().items() if k != "prerelease"},
        ):
            with self.subTest(payload=str(payload)[:60]), self.assertRaises(MalformedGitHubPayloadError):
                normalize_release(payload, FIXTURE_REF)


class ActivityCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def provider(self, transport: FakeTransport) -> LiveGitHubProvider:
        return LiveGitHubProvider(
            transport, InMemoryTTLCache(), clock=self.clock, sleep=self.sleep, jitter=lambda: 0.0
        )

    @staticmethod
    def script(**overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            REPO: ok(repo_payload()),
            LANGS: ok(languages_payload()),
            CONTRIBUTORS: ok(contributors_payload()),
            RELEASE: ok(release_payload()),
        }
        base.update(overrides)
        return base


class WhatItReads(ActivityCase):
    async def test_a_healthy_repository_costs_three_requests_in_a_fixed_order(self) -> None:
        transport = FakeTransport(self.script())
        result = await self.provider(transport).repository_activity(FIXTURE_REF)
        self.assertEqual(transport.paths(), [REPO, CONTRIBUTORS, RELEASE])
        self.assertEqual(result.full_name, "fixture-org/example-repo")
        self.assertIs(result.contributors_status, ContributorsStatus.LISTED)
        self.assertEqual(
            [c.login for c in result.contributors], ["fixture-person-a", "fixture-person-b", "fixture-helper[bot]"]
        )
        assert result.latest_release is not None
        self.assertEqual(result.latest_release.tag_name, "v1.2.3")
        self.assertIs(result.cache_status, CacheStatus.FRESH)
        self.assertEqual(
            list(result.source_urls),
            [
                "https://api.github.com/repos/fixture-org/example-repo",
                "https://api.github.com/repos/fixture-org/example-repo/contributors?per_page=10",
                "https://api.github.com/repos/fixture-org/example-repo/releases/latest",
            ],
        )

    async def test_after_the_basic_analysis_the_repository_is_not_asked_again_within_a_minute(self) -> None:
        transport = FakeTransport(self.script())
        provider = self.provider(transport)
        await provider.analyze_repository(FIXTURE_REF)
        self.clock.now += 10
        await provider.repository_activity(FIXTURE_REF)
        self.assertEqual(transport.paths(), [REPO, LANGS, CONTRIBUTORS, RELEASE])  # two more, not three

    async def test_after_a_minute_the_repository_is_asked_again_with_its_validator(self) -> None:
        transport = FakeTransport(self.script(**{REPO: [ok(repo_payload()), NOT_MODIFIED]}))
        provider = self.provider(transport)
        await provider.analyze_repository(FIXTURE_REF)
        self.clock.now += 120
        await provider.repository_activity(FIXTURE_REF)
        self.assertEqual(transport.paths(), [REPO, LANGS, REPO, CONTRIBUTORS, RELEASE])
        self.assertEqual(transport.calls[2][1], {"if-none-match": '"fixture-etag"'})

    async def test_a_private_repository_is_refused_before_contributors_or_releases_are_asked(self) -> None:
        transport = FakeTransport(self.script(**{REPO: ok(repo_payload(private=True))}))
        with self.assertRaises(GitHubNotFoundError):
            await self.provider(transport).repository_activity(FIXTURE_REF)
        self.assertEqual(transport.paths(), [REPO])

    async def test_a_repository_that_is_not_there_stops_after_the_first_request(self) -> None:
        transport = FakeTransport(self.script(**{REPO: error(404, "Not Found")}))
        with self.assertRaises(GitHubNotFoundError):
            await self.provider(transport).repository_activity(FIXTURE_REF)
        self.assertEqual(transport.paths(), [REPO])

    async def test_the_request_uses_the_callers_spelling_and_the_link_githubs(self) -> None:
        typed = RepoRef("FIXTURE-ORG", "Example-Repo")
        transport = FakeTransport({
            typed.api_path: ok(repo_payload()),
            f"{typed.api_path}/contributors?per_page=10": ok([]),
            f"{typed.api_path}/releases/latest": ok(release_payload(html_url="")),
        })  # fmt: skip
        result = await self.provider(transport).repository_activity(typed)
        assert result.latest_release is not None
        self.assertEqual(result.full_name, "fixture-org/example-repo")
        self.assertEqual(result.latest_release.html_url, "https://github.com/fixture-org/example-repo/releases")


class WhatGitHubSaysThatIsNotAnError(ActivityCase):
    async def test_204_means_nothing_to_list_and_is_not_kept_in_the_cache(self) -> None:
        transport = FakeTransport(self.script(**{CONTRIBUTORS: NO_CONTENT}))
        provider = self.provider(transport)
        first = await provider.repository_activity(FIXTURE_REF)
        self.assertIs(first.contributors_status, ContributorsStatus.NONE)
        self.assertEqual(first.contributors, ())
        self.clock.now += 5
        again = await provider.repository_activity(FIXTURE_REF)
        self.assertIs(again.contributors_status, ContributorsStatus.NONE)  # not an empty 200 served from the cache
        self.assertEqual(transport.paths().count(CONTRIBUTORS), 2)  # asked again: a 204 is never stored

    async def test_an_empty_list_is_also_none(self) -> None:
        transport = FakeTransport(self.script(**{CONTRIBUTORS: ok([])}))
        result = await self.provider(transport).repository_activity(FIXTURE_REF)
        self.assertIs(result.contributors_status, ContributorsStatus.NONE)

    async def test_a_list_of_only_anonymous_entries_is_none_and_keeps_no_email(self) -> None:
        anonymous = [{"type": "Anonymous", "email": "x@example.invalid", "name": "X", "contributions": 3}]
        result = await self.provider(FakeTransport(self.script(**{CONTRIBUTORS: ok(anonymous)}))).repository_activity(
            FIXTURE_REF
        )
        self.assertIs(result.contributors_status, ContributorsStatus.NONE)
        self.assertNotIn("example.invalid", repr(result))

    async def test_403_on_the_contributor_list_is_declined_and_the_release_is_still_read(self) -> None:
        message = "The history or contributor list is too large to list contributors for this repository via the API."
        transport = FakeTransport(self.script(**{CONTRIBUTORS: error(403, message)}))
        result = await self.provider(transport).repository_activity(FIXTURE_REF)
        self.assertIs(result.contributors_status, ContributorsStatus.DECLINED)
        self.assertEqual(result.contributors, ())
        self.assertIsNotNone(result.latest_release)
        self.assertEqual(transport.paths(), [REPO, CONTRIBUTORS, RELEASE])  # no retry of a plain 403
        self.assertEqual(len(result.source_urls), 3)  # the refusal is something VYRON read, so it is named

    async def test_404_on_the_latest_release_is_no_release_and_the_contributors_are_kept(self) -> None:
        transport = FakeTransport(self.script(**{RELEASE: error(404, "Not Found")}))
        result = await self.provider(transport).repository_activity(FIXTURE_REF)
        self.assertIsNone(result.latest_release)
        self.assertIs(result.contributors_status, ContributorsStatus.LISTED)
        self.assertEqual(len(result.source_urls), 3)

    async def test_a_young_empty_repository_has_neither(self) -> None:
        transport = FakeTransport(self.script(**{CONTRIBUTORS: NO_CONTENT, RELEASE: error(404, "Not Found")}))
        result = await self.provider(transport).repository_activity(FIXTURE_REF)
        self.assertEqual((result.contributors_status, result.contributors, result.latest_release),
                         (ContributorsStatus.NONE, (), None))  # fmt: skip

    async def test_the_time_of_a_reported_refusal_counts_toward_fetched_at(self) -> None:
        transport = FakeTransport(self.script(**{RELEASE: error(404, "Not Found")}))
        provider = self.provider(transport)
        await provider.analyze_repository(FIXTURE_REF)  # the repository copy is stored at T0
        self.clock.now += 30  # still fresh, so the repository is served from the cache with its own time
        result = await provider.repository_activity(FIXTURE_REF)
        self.assertEqual(result.fetched_at.timestamp(), T0)  # never newer than the oldest read


class WhatStillFails(ActivityCase):
    async def test_a_rate_limit_on_the_contributor_list_is_not_declined(self) -> None:
        limited = error(
            403, "API rate limit exceeded", **{"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(T0) + 3600)}
        )
        transport = FakeTransport(self.script(**{CONTRIBUTORS: limited}))
        with self.assertRaises(GitHubRateLimitedError):
            await self.provider(transport).repository_activity(FIXTURE_REF)
        self.assertNotIn(RELEASE, transport.paths())

    async def test_a_failing_github_is_reported_not_turned_into_an_empty_answer(self) -> None:
        transport = FakeTransport(self.script(**{CONTRIBUTORS: error(503, "down")}))
        with self.assertRaises(GitHubUnavailableError):
            await self.provider(transport).repository_activity(FIXTURE_REF)

    async def test_a_404_on_the_contributor_list_after_the_repository_was_read_is_not_found(self) -> None:
        transport = FakeTransport(self.script(**{CONTRIBUTORS: error(404, "Not Found")}))
        with self.assertRaises(GitHubNotFoundError):
            await self.provider(transport).repository_activity(FIXTURE_REF)

    async def test_a_malformed_contributor_list_is_refused(self) -> None:
        transport = FakeTransport(self.script(**{CONTRIBUTORS: ok({"message": "not a list"})}))
        with self.assertRaises(MalformedGitHubPayloadError):
            await self.provider(transport).repository_activity(FIXTURE_REF)

    async def test_a_draft_as_the_latest_release_is_refused(self) -> None:
        transport = FakeTransport(self.script(**{RELEASE: ok(release_payload(draft=True))}))
        with self.assertRaises(MalformedGitHubPayloadError):
            await self.provider(transport).repository_activity(FIXTURE_REF)

    async def test_an_accepted_but_empty_202_is_not_taken_for_a_list(self) -> None:
        accepted = RawResponse(202, {}, b"")  # GitHub uses 202 elsewhere for "still computing"
        transport = FakeTransport(self.script(**{CONTRIBUTORS: accepted}))
        with self.assertRaises(GitHubError):
            await self.provider(transport).repository_activity(FIXTURE_REF)


class HowCurrentTheAnswerIs(ActivityCase):
    async def test_a_second_read_after_a_minute_is_revalidated_with_the_validators_it_stored(self) -> None:
        script = self.script(**{
            REPO: [ok(repo_payload()), NOT_MODIFIED],
            CONTRIBUTORS: [ok(contributors_payload()), NOT_MODIFIED],
            RELEASE: [ok(release_payload()), NOT_MODIFIED],
        })  # fmt: skip
        transport = FakeTransport(script)
        provider = self.provider(transport)
        await provider.repository_activity(FIXTURE_REF)
        self.clock.now += 120
        again = await provider.repository_activity(FIXTURE_REF)
        self.assertIs(again.cache_status, CacheStatus.REVALIDATED)
        self.assertEqual(len(again.contributors), 3)  # the stored body is served on a 304
        self.assertEqual(transport.calls[4], (CONTRIBUTORS, {"if-none-match": '"fixture-etag"'}))
        self.assertEqual(transport.calls[5], (RELEASE, {"if-none-match": '"fixture-etag"'}))

    async def test_a_confirmed_repository_next_to_parts_read_for_the_first_time_is_fresh(self) -> None:
        # What the first real run did: the basic analysis, then (after a minute) the opt-in read. The repository
        # copy is confirmed by a 304, the list and the release are read for the first time: "nothing had changed
        # since the previous read" would be false of both.
        script = self.script(**{REPO: [ok(repo_payload()), NOT_MODIFIED]})
        provider = self.provider(FakeTransport(script))
        await provider.analyze_repository(FIXTURE_REF)
        self.clock.now += 120
        result = await provider.repository_activity(FIXTURE_REF)
        self.assertIs(result.cache_status, CacheStatus.FRESH)
        self.assertEqual(result.fetched_at.timestamp(), T0 + 120)

    async def test_a_confirmed_list_next_to_a_release_that_does_not_exist_is_fresh(self) -> None:
        missing = error(404, "Not Found")
        script = self.script(**{
            REPO: [ok(repo_payload()), NOT_MODIFIED],
            CONTRIBUTORS: [ok(contributors_payload()), NOT_MODIFIED],
            RELEASE: missing,
        })  # fmt: skip
        provider = self.provider(FakeTransport(script))
        await provider.repository_activity(FIXTURE_REF)
        self.clock.now += 120
        again = await provider.repository_activity(FIXTURE_REF)
        self.assertIsNone(again.latest_release)
        self.assertIs(again.cache_status, CacheStatus.FRESH)  # the 404 was read just now and is never stored

    async def test_a_confirmed_repository_next_to_a_declined_list_is_fresh(self) -> None:
        refusal = error(403, "The history or contributor list is too large to list contributors.")
        script = self.script(**{
            REPO: [ok(repo_payload()), NOT_MODIFIED],
            CONTRIBUTORS: refusal,
            RELEASE: [ok(release_payload()), NOT_MODIFIED],
        })  # fmt: skip
        provider = self.provider(FakeTransport(script))
        await provider.repository_activity(FIXTURE_REF)
        self.clock.now += 120
        again = await provider.repository_activity(FIXTURE_REF)
        self.assertIs(again.contributors_status, ContributorsStatus.DECLINED)
        self.assertIs(again.cache_status, CacheStatus.FRESH)

    async def test_a_list_confirmed_with_no_content_is_fresh_because_a_204_is_never_stored(self) -> None:
        script = self.script(**{REPO: [ok(repo_payload()), NOT_MODIFIED], CONTRIBUTORS: NO_CONTENT})
        provider = self.provider(FakeTransport(script))
        await provider.repository_activity(FIXTURE_REF)
        self.clock.now += 120
        again = await provider.repository_activity(FIXTURE_REF)
        self.assertIs(again.contributors_status, ContributorsStatus.NONE)
        self.assertIs(again.cache_status, CacheStatus.FRESH)

    async def test_an_unreachable_github_serves_the_stored_list_and_says_stale(self) -> None:
        script = self.script(**{
            REPO: [ok(repo_payload()), NOT_MODIFIED],
            CONTRIBUTORS: [ok(contributors_payload()), OSError("offline")],
            RELEASE: [ok(release_payload()), NOT_MODIFIED],
        })  # fmt: skip
        provider = self.provider(FakeTransport(script))
        await provider.repository_activity(FIXTURE_REF)
        self.clock.now += 120
        later = await provider.repository_activity(FIXTURE_REF)
        self.assertIs(later.cache_status, CacheStatus.STALE)  # one stale read makes the whole answer stale
        self.assertEqual([c.login for c in later.contributors][:1], ["fixture-person-a"])
        self.assertEqual(later.fetched_at.timestamp(), T0)  # the stale copy's time, not the time of the call

    async def test_nothing_is_stored_for_a_declined_list_so_a_later_success_is_seen(self) -> None:
        refusal = error(403, "The history or contributor list is too large to list contributors.")
        provider = self.provider(FakeTransport(self.script(**{CONTRIBUTORS: [refusal, ok(contributors_payload())]})))
        first = await provider.repository_activity(FIXTURE_REF)
        self.clock.now += 5
        second = await provider.repository_activity(FIXTURE_REF)
        self.assertIs(first.contributors_status, ContributorsStatus.DECLINED)
        self.assertIs(second.contributors_status, ContributorsStatus.LISTED)


class TheRoute(unittest.TestCase):
    def live_app(self, script: dict[str, Any]) -> Any:
        async def no_sleep(seconds: float) -> None:
            return None

        provider = LiveGitHubProvider(
            FakeTransport(script), InMemoryTTLCache(), clock=Clock(), sleep=no_sleep, jitter=lambda: 0.0
        )
        return make_app(github=provider)

    def test_the_answer_matches_its_schema_and_says_where_it_came_from(self) -> None:
        response, body = get(make_app(), ACTIVITY_URL)
        self.assertEqual(response.status, 200, body)
        self.assertEqual(validate(body, COMPONENTS["RepositoryActivity"], COMPONENTS), [])
        self.assertEqual(body["provenance"], "sourced")
        self.assertEqual(body["html_url"], "https://github.com/fixture-org/example-repo")
        self.assertEqual(len(body["source_urls"]), 3)

    def test_a_live_read_exposes_only_login_count_bot_flag_and_a_profile_link(self) -> None:
        app = self.live_app(ActivityCase.script())
        response, body = get(app, ACTIVITY_URL)
        self.assertEqual(response.status, 200, body)
        self.assertEqual(validate(body, COMPONENTS["RepositoryActivity"], COMPONENTS), [])
        self.assertEqual(
            set(body["contributors"][0]), {"login", "contributions", "is_bot", "profile_url"}
        )
        self.assertEqual(body["contributors"][0]["profile_url"], "https://github.com/fixture-person-a")
        self.assertEqual([c["is_bot"] for c in body["contributors"]], [False, False, True])
        self.assertIsNone(body["contributors"][2]["profile_url"])
        self.assertEqual([c["contributions"] for c in body["contributors"]], [120, 45, 30])
        text = json.dumps(body)
        for private in ("avatars.githubusercontent", "node_id", "site_admin", "Release notes are never read"):
            self.assertNotIn(private, text)
        self.assertEqual(
            set(body["latest_release"]), {"tag_name", "name", "published_at", "prerelease", "html_url"}
        )

    def test_no_release_is_null_and_no_contributors_is_stated_not_implied(self) -> None:
        script = ActivityCase.script(**{CONTRIBUTORS: NO_CONTENT, RELEASE: error(404, "Not Found")})
        response, body = get(self.live_app(script), ACTIVITY_URL)
        self.assertEqual(response.status, 200, body)
        self.assertEqual(validate(body, COMPONENTS["RepositoryActivity"], COMPONENTS), [])
        self.assertEqual((body["contributors_status"], body["contributors"], body["latest_release"]),
                         ("none", [], None))  # fmt: skip

    def test_a_declined_list_is_a_200_that_says_declined(self) -> None:
        script = ActivityCase.script(**{CONTRIBUTORS: error(403, "The history or contributor list is too large")})
        response, body = get(self.live_app(script), ACTIVITY_URL)
        self.assertEqual((response.status, body["contributors_status"]), (200, "declined"))

    def test_a_private_repository_is_not_found_and_nothing_else_is_asked(self) -> None:
        transport = FakeTransport(ActivityCase.script(**{REPO: ok(repo_payload(private=True))}))

        async def no_sleep(seconds: float) -> None:
            return None

        provider = LiveGitHubProvider(transport, InMemoryTTLCache(), clock=Clock(), sleep=no_sleep)
        response, body = get(make_app(github=provider), ACTIVITY_URL)
        self.assertEqual((response.status, body["error"]["code"]), (404, "not_found"))
        self.assertEqual(transport.paths(), [REPO])

    def test_names_that_are_not_repositories_never_reach_the_provider(self) -> None:
        provider = FakeGitHubProvider()
        app = make_app(github=provider)
        for owner, repo in (("github.com", "vercel"), ("-bad", "repo"), ("owner", ".."), ("owner", "a b")):
            with self.subTest(owner=owner, repo=repo):
                response, body = get(app, f"/api/v1/github/repositories/{owner}/{repo}/activity")
                self.assertIn(response.status, (400, 404, 422))
                self.assertIsNotNone(body)
        self.assertEqual(provider.requested, [])

    def test_with_github_switched_off_it_says_so(self) -> None:
        response, body = get(make_app(github=None), ACTIVITY_URL)
        self.assertEqual((response.status, body["error"]["code"]), (503, "service_unavailable"))

    def test_failures_use_the_same_fixed_messages_as_the_basic_analysis(self) -> None:
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
                    response, body = get(app, ACTIVITY_URL)
                self.assertEqual((response.status, body["error"]["code"]), (status, code))
                self.assertNotIn("secret detail", json.dumps(body))

    def test_the_route_is_cached_for_a_minute(self) -> None:
        response, _ = get(make_app(), ACTIVITY_URL)
        self.assertEqual(response.headers["Cache-Control"], "public, max-age=60")


if __name__ == "__main__":
    unittest.main()
