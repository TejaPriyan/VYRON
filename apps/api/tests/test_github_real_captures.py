"""VYRON's GitHub path against responses GitHub really sent (see fixtures/github_real/README.md).

Unlike every other GitHub test here, the payloads were not written by hand: they are copies of what
api.github.com answered to unauthenticated requests on 2026-10-26, 2026-10-27, 2026-11-02 and 2026-11-04. These tests
check
how VYRON reads and handles those answers. The figures they name (stars, sizes) are "as captured", not current.
The captures were made by the project owner and relayed to me; I did not make the requests.
"""

import itertools
import json
import unittest
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from github_fakes import FakeTransport
from test_api_app import get, make_app
from vyron_api.adapters.memory_cache import InMemoryTTLCache
from vyron_api.api.schemas import COMPONENTS
from vyron_api.api.validate import validate
from vyron_api.github.budget import RateLimitBudget
from vyron_api.github.normalize import normalize_commit_activity, normalize_rate_limit
from vyron_api.github.provider import LiveGitHubProvider
from vyron_api.github.retry import RawResponse
from vyron_api.github.url import RepoRef
from vyron_api.providers.github import (
    CacheStatus,
    CommitActivityStatus,
    ContributorsStatus,
    GitHubMovedError,
    GitHubNotFoundError,
)

ROOT = Path(__file__).resolve().parent / "fixtures" / "github_real"
FLASK, ATOM, NODE, ACTIVITY, LINUX, STATS = (
    ROOT / name
    for name in (
        "pallets_flask", "atom_atom_archived", "joyent_node_renamed", "pallets_flask_activity", "torvalds_linux_huge",
        "commit_activity_flask_linux",
    )
)  # fmt: skip
MISSING = "repos__vyron-check-owner-that-does-not-exist__no-such-repository.json"
T0 = datetime(2026, 10, 26, 7, 53, 30, tzinfo=UTC).timestamp()
HEADER_ALLOW_LIST = {
    "cache-control", "content-type", "etag", "last-modified", "link", "location", "x-github-api-version-selected",
    "x-github-media-type", "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset", "x-ratelimit-resource",
    "x-ratelimit-used",
}  # fmt: skip


def capture(folder: Path, name: str) -> dict[str, Any]:
    return json.loads((folder / name).read_text(encoding="utf-8"))


def raw(folder: Path, name: str) -> RawResponse:
    data = capture(folder, name)
    return RawResponse(data["status"], dict(data["headers"]), json.dumps(data["body"]).encode("utf-8"))


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> float:
        return self.now


def provider_for(transport: FakeTransport, clock: Clock) -> LiveGitHubProvider:
    async def no_sleep(seconds: float) -> None:
        return None

    return LiveGitHubProvider(transport, InMemoryTTLCache(), clock=clock, sleep=no_sleep, jitter=lambda: 0.0)


class TheCapturesAreWhatTheyClaimToBe(unittest.TestCase):
    def test_each_folder_says_it_is_real_unauthenticated_github_data(self) -> None:
        for folder in (FLASK, ATOM, NODE, ACTIVITY, LINUX, STATS):
            with self.subTest(folder=folder.name):
                manifest = capture(folder, "MANIFEST.json")
                self.assertEqual(manifest["data"], "REAL GITHUB DATA")
                self.assertEqual(manifest["source"], "https://api.github.com")
                self.assertIs(manifest["authenticated"], False)
                on_disk = sorted(p.name for p in folder.glob("*.json") if p.name != "MANIFEST.json")
                self.assertEqual(sorted(manifest["files"]), on_disk)

    def test_only_allow_listed_headers_were_kept_and_no_credential_is_present(self) -> None:
        for folder in (FLASK, ATOM, NODE, ACTIVITY, LINUX, STATS):
            for path in sorted(folder.glob("*.json")):
                text = path.read_text(encoding="utf-8")
                for marker in ("ghp_", "gho_", "github_pat_", "Bearer ", "authorization", "set-cookie"):
                    self.assertNotIn(marker, text, f"{path.name} contains {marker!r}")
                if path.name != "MANIFEST.json":
                    self.assertLessEqual(set(capture(folder, path.name)["headers"]), HEADER_ALLOW_LIST, path.name)


class AHealthyActiveRepository(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.transport = FakeTransport({
            "/repos/pallets/flask": raw(FLASK, "repos__pallets__flask.json"),
            "/repos/pallets/flask/languages": raw(FLASK, "repos__pallets__flask__languages.json"),
        })  # fmt: skip
        self.provider = provider_for(self.transport, self.clock)

    async def test_the_real_payloads_are_read_into_the_snapshot_as_captured(self) -> None:
        result = await self.provider.analyze_repository(RepoRef("pallets", "flask"))
        s = result.snapshot
        self.assertEqual((s.full_name, s.default_branch, s.primary_language), ("pallets/flask", "main", "Python"))
        self.assertEqual((s.stars, s.forks, s.open_issues), (75088, 17132, 4))
        self.assertEqual((s.archived, s.is_fork, s.license_spdx), (False, False, "BSD-3-Clause"))
        self.assertEqual(s.topics, ("flask", "jinja", "pallets", "python", "web-framework", "werkzeug", "wsgi"))
        self.assertEqual(s.created_at, datetime(2010, 4, 6, 11, 11, 59, tzinfo=UTC))
        self.assertEqual(s.pushed_at, datetime(2026, 10, 7, 14, 2, 59, tzinfo=UTC))
        self.assertEqual(result.languages, {"CSS": 18, "HTML": 405, "Python": 565106, "Shell": 193})
        self.assertEqual(result.cache_status, CacheStatus.FRESH)
        self.assertEqual(self.transport.paths(), ["/repos/pallets/flask", "/repos/pallets/flask/languages"])

    async def test_a_weak_etag_is_sent_back_exactly_as_github_wrote_it(self) -> None:
        repo_etag = capture(FLASK, "repos__pallets__flask.json")["headers"]["etag"]
        languages_etag = capture(FLASK, "repos__pallets__flask__languages.json")["headers"]["etag"]
        self.assertTrue(repo_etag.startswith('W/"'))  # real ETags here are weak; hand-written fixtures were strong
        quota = {"x-ratelimit-remaining": "40"}
        not_modified = RawResponse(304, quota, b"")
        transport = FakeTransport({
            "/repos/pallets/flask": [raw(FLASK, "repos__pallets__flask.json"), not_modified],
            "/repos/pallets/flask/languages": [raw(FLASK, "repos__pallets__flask__languages.json"), not_modified],
        })  # fmt: skip
        provider = provider_for(transport, self.clock)
        await provider.analyze_repository(RepoRef("pallets", "flask"))
        self.clock.now = T0 + 120
        again = await provider.analyze_repository(RepoRef("pallets", "flask"))
        self.assertEqual(again.cache_status, CacheStatus.REVALIDATED)
        repo_modified = capture(FLASK, "repos__pallets__flask.json")["headers"]["last-modified"]
        languages_modified = capture(FLASK, "repos__pallets__flask__languages.json")["headers"]["last-modified"]
        self.assertEqual(  # the real responses carry both validators, so both are sent back
            transport.calls[2],
            ("/repos/pallets/flask", {"if-none-match": repo_etag, "if-modified-since": repo_modified}),
        )
        self.assertEqual(
            transport.calls[3],
            (
                "/repos/pallets/flask/languages",
                {"if-none-match": languages_etag, "if-modified-since": languages_modified},
            ),
        )
        self.assertEqual(again.snapshot.stars, 75088)  # the stored body is served on a 304

    def test_the_api_route_answers_in_its_own_schema_from_the_real_payloads(self) -> None:
        app = make_app(github=self.provider)
        response, body = get(app, "/api/v1/github/repositories/pallets/flask")
        self.assertEqual(response.status, 200, body)
        self.assertEqual(validate(body, COMPONENTS["RepositoryAnalysis"], COMPONENTS), [])
        self.assertEqual((body["full_name"], body["stars"]), ("pallets/flask", 75088))
        licence = (body["license_spdx"], body["license_name"])
        self.assertEqual(licence, ("BSD-3-Clause", 'BSD 3-Clause "New" or "Revised" License'))
        self.assertEqual(body["html_url"], "https://github.com/pallets/flask")
        self.assertEqual(body["provenance"], "sourced")
        self.assertEqual(sum(body["languages"].values()), 18 + 405 + 565106 + 193)

    async def test_the_quota_headers_on_a_real_response_feed_the_budget(self) -> None:
        budget = RateLimitBudget(now=self.clock)
        budget.update(capture(FLASK, "repos__pallets__flask.json")["headers"])
        core = budget.snapshot()["core"]
        self.assertEqual((core.limit, core.remaining, core.used), (60, 41, 19))


CONTRIBUTORS = "/repos/pallets/flask/contributors?per_page=10"
RELEASE = "/repos/pallets/flask/releases/latest"
CONTRIBUTORS_FILE = "repos__pallets__flask__contributors__per_page-10.json"
RELEASE_FILE = "repos__pallets__flask__releases__latest.json"
FLASK_TOP_TEN = [
    ("davidism", 1865), ("mitsuhiko", 1189), ("untitaker", 274), ("rduplain", 122), ("greyli", 105),
    ("dependabot-preview[bot]", 91), ("DasIch", 86), ("dependabot[bot]", 61), ("kennethreitz", 59), ("pgjones", 50),
]  # fmt: skip


def activity_script() -> Mapping[str, RawResponse]:
    return {
        "/repos/pallets/flask": raw(ACTIVITY, "repos__pallets__flask.json"),
        CONTRIBUTORS: raw(ACTIVITY, CONTRIBUTORS_FILE),
        RELEASE: raw(ACTIVITY, RELEASE_FILE),
    }


class TheTopContributorsAndTheLatestRelease(unittest.IsolatedAsyncioTestCase):
    """What GitHub really answered for ``pallets/flask`` on 2026-11-02, read by the same code as every other answer."""

    def setUp(self) -> None:
        self.clock = Clock()
        self.transport = FakeTransport(activity_script())
        self.provider = provider_for(self.transport, self.clock)

    async def test_the_real_payloads_are_read_as_captured(self) -> None:
        result = await self.provider.repository_activity(RepoRef("pallets", "flask"))
        self.assertIs(result.contributors_status, ContributorsStatus.LISTED)
        self.assertEqual([(c.login, c.contributions) for c in result.contributors], FLASK_TOP_TEN)
        by_login = {c.login: c for c in result.contributors}
        self.assertEqual(by_login["davidism"].profile_url, "https://github.com/davidism")
        self.assertEqual((by_login["dependabot[bot]"].is_bot, by_login["dependabot[bot]"].profile_url), (True, None))
        self.assertEqual(sum(c.is_bot for c in result.contributors), 2)
        release = result.latest_release
        assert release is not None
        self.assertEqual((release.tag_name, release.prerelease), ("3.1.3", False))
        self.assertEqual(release.published_at, datetime(2026, 2, 19, 5, 1, 30, tzinfo=UTC))
        self.assertEqual(release.html_url, "https://github.com/pallets/flask/releases/tag/3.1.3")
        self.assertIs(result.cache_status, CacheStatus.FRESH)
        self.assertEqual(self.transport.paths(), ["/repos/pallets/flask", CONTRIBUTORS, RELEASE])

    async def test_a_release_with_an_empty_name_has_no_name_rather_than_a_blank_one(self) -> None:
        self.assertEqual(capture(ACTIVITY, RELEASE_FILE)["body"]["name"], "")  # what GitHub sent: not null, empty
        result = await self.provider.repository_activity(RepoRef("pallets", "flask"))
        assert result.latest_release is not None
        self.assertIsNone(result.latest_release.name)

    def test_what_the_real_answers_have_that_the_hand_written_fixtures_did_not(self) -> None:
        contributors, release = capture(ACTIVITY, CONTRIBUTORS_FILE), capture(ACTIVITY, RELEASE_FILE)
        self.assertEqual(len(contributors["body"]), 10)
        self.assertIn('rel="next"', contributors["headers"]["link"])  # more pages exist; VYRON reads only the first
        self.assertIn('page=40>; rel="last"', contributors["headers"]["link"])
        for answer in (contributors, release):
            self.assertTrue(answer["headers"]["etag"].startswith('W/"'))  # weak, like the repository's
            self.assertIn("last-modified", answer["headers"])  # the written fixtures had none
        self.assertIn("user_view_type", contributors["body"][0])  # a field VYRON does not read
        self.assertIn("immutable", release["body"])  # likewise
        self.assertEqual(contributors["body"], sorted(contributors["body"], key=lambda e: -e["contributions"]))
        self.assertNotIn("email", json.dumps(contributors["body"]))  # an email is never in this answer

    def test_the_route_answers_in_its_own_schema_from_the_real_payloads(self) -> None:
        app = make_app(github=self.provider)
        response, body = get(app, "/api/v1/github/repositories/pallets/flask/activity")
        self.assertEqual(response.status, 200, body)
        self.assertEqual(validate(body, COMPONENTS["RepositoryActivity"], COMPONENTS), [])
        self.assertEqual(body["contributors_status"], "listed")
        self.assertEqual(len(body["contributors"]), 10)
        self.assertIsNone(body["latest_release"]["name"])
        self.assertEqual(body["provenance"], "sourced")
        self.assertEqual(len(body["source_urls"]), 3)
        self.assertNotIn("avatar", json.dumps(body))

    async def test_both_validators_are_sent_back_exactly_as_github_wrote_them(self) -> None:
        quota = {"x-ratelimit-remaining": "40"}
        not_modified = RawResponse(304, quota, b"")
        script: Mapping[str, Sequence[RawResponse]] = {
            path: [response, not_modified] for path, response in activity_script().items()
        }
        transport = FakeTransport(script)
        provider = provider_for(transport, self.clock)
        await provider.repository_activity(RepoRef("pallets", "flask"))
        self.clock.now = T0 + 120
        again = await provider.repository_activity(RepoRef("pallets", "flask"))
        self.assertIs(again.cache_status, CacheStatus.REVALIDATED)
        self.assertEqual([c.login for c in again.contributors], [login for login, _ in FLASK_TOP_TEN])
        for position, (path, file) in {4: (CONTRIBUTORS, CONTRIBUTORS_FILE), 5: (RELEASE, RELEASE_FILE)}.items():
            headers = capture(ACTIVITY, file)["headers"]
            self.assertEqual(
                transport.calls[position],
                (path, {"if-none-match": headers["etag"], "if-modified-since": headers["last-modified"]}),
            )

    async def test_after_the_basic_analysis_a_minute_later_the_answer_is_live_not_confirmed(self) -> None:
        # The owner's first click on the real page: analysis, then the opt-in read more than a minute later.
        # The repository came back 304; the list and the release were read for the first time.
        not_modified = RawResponse(304, {"x-ratelimit-remaining": "40"}, b"")
        script: Mapping[str, RawResponse | Sequence[RawResponse]] = {
            "/repos/pallets/flask": [raw(ACTIVITY, "repos__pallets__flask.json"), not_modified],
            "/repos/pallets/flask/languages": raw(ACTIVITY, "repos__pallets__flask__languages.json"),
            CONTRIBUTORS: raw(ACTIVITY, CONTRIBUTORS_FILE),
            RELEASE: raw(ACTIVITY, RELEASE_FILE),
        }
        provider = provider_for(FakeTransport(script), self.clock)
        await provider.analyze_repository(RepoRef("pallets", "flask"))
        self.clock.now = T0 + 120
        result = await provider.repository_activity(RepoRef("pallets", "flask"))
        self.assertIs(result.cache_status, CacheStatus.FRESH)
        self.assertEqual(len(result.contributors), 10)

    def test_each_of_the_four_reads_cost_one_request_and_the_budget_reads_the_last(self) -> None:
        used = [
            int(capture(ACTIVITY, file)["headers"]["x-ratelimit-used"])
            for file in ("repos__pallets__flask.json", "repos__pallets__flask__languages.json", CONTRIBUTORS_FILE,
                         RELEASE_FILE)
        ]  # fmt: skip
        self.assertEqual(used, [7, 8, 9, 10])  # one more each time: the documented cost of the opt-in read is two
        budget = RateLimitBudget(now=self.clock)
        budget.update(capture(ACTIVITY, RELEASE_FILE)["headers"])
        core = budget.snapshot()["core"]
        self.assertEqual((core.limit, core.remaining, core.used), (60, 50, 10))

    async def test_a_repository_that_does_not_exist_is_not_found_before_anything_else_is_asked(self) -> None:
        name = "/repos/vyron-check-owner-that-does-not-exist/no-such-repository"
        transport = FakeTransport({name: raw(ACTIVITY, MISSING)})
        with self.assertRaises(GitHubNotFoundError):
            await provider_for(transport, self.clock).repository_activity(
                RepoRef("vyron-check-owner-that-does-not-exist", "no-such-repository")
            )
        self.assertEqual(transport.paths(), [name])


REPO_PATH = "/repos/torvalds/linux"
LIST_PATH = f"{REPO_PATH}/contributors?per_page=10"
RELEASE_PATH = f"{REPO_PATH}/releases/latest"
LIST_FILE = "repos__torvalds__linux__contributors__per_page-10.json"
RELEASE_FILE_LINUX = "repos__torvalds__linux__releases__latest.json"


class AHugeRepositoryWithoutReleases(unittest.IsolatedAsyncioTestCase):
    """``torvalds/linux`` on 2026-11-02: no contributor list, no release, and a licence GitHub calls "Other"."""

    def setUp(self) -> None:
        self.clock = Clock()
        self.transport = FakeTransport(
            {
                REPO_PATH: raw(LINUX, "repos__torvalds__linux.json"),
                f"{REPO_PATH}/languages": raw(LINUX, "repos__torvalds__linux__languages.json"),
                LIST_PATH: raw(LINUX, LIST_FILE),
                RELEASE_PATH: raw(LINUX, RELEASE_FILE_LINUX),
            }
        )
        self.provider = provider_for(self.transport, self.clock)
        self.ref = RepoRef("torvalds", "linux")

    def test_what_githubs_refusal_and_its_missing_release_really_look_like(self) -> None:
        refusal = capture(LINUX, LIST_FILE)
        self.assertEqual(refusal["status"], 403)
        self.assertEqual(
            refusal["body"]["message"],
            "The history or contributor list is too large to list contributors for this repository via the API.",
        )
        self.assertNotIn("retry-after", refusal["headers"])  # it is not a rate limit: no wait is asked for
        self.assertGreater(int(refusal["headers"]["x-ratelimit-remaining"]), 0)
        missing = capture(LINUX, RELEASE_FILE_LINUX)
        self.assertEqual((missing["status"], missing["body"]["message"]), (404, "Not Found"))

    async def test_the_refusal_is_declined_not_a_rate_limit_and_the_missing_release_is_none(self) -> None:
        result = await self.provider.repository_activity(self.ref)
        self.assertIs(result.contributors_status, ContributorsStatus.DECLINED)
        self.assertEqual(result.contributors, ())
        self.assertIsNone(result.latest_release)
        self.assertIs(result.cache_status, CacheStatus.FRESH)
        self.assertEqual(self.transport.paths(), [REPO_PATH, LIST_PATH, RELEASE_PATH])  # each once: no retry

    async def test_neither_answer_is_stored_so_both_are_asked_again(self) -> None:
        await self.provider.repository_activity(self.ref)
        self.clock.now += 10
        again = await self.provider.repository_activity(self.ref)
        self.assertIs(again.contributors_status, ContributorsStatus.DECLINED)
        self.assertEqual(len(self.transport.paths()), 5)  # the repository came from the cache; the other two did not

    def test_the_route_answers_in_its_schema_and_never_repeats_githubs_words(self) -> None:
        app = make_app(github=self.provider)
        response, body = get(app, "/api/v1/github/repositories/torvalds/linux/activity")
        self.assertEqual(response.status, 200, body)
        self.assertEqual(validate(body, COMPONENTS["RepositoryActivity"], COMPONENTS), [])
        self.assertEqual(body["contributors_status"], "declined")
        self.assertEqual((body["contributors"], body["latest_release"]), ([], None))
        self.assertNotIn("too large", json.dumps(body))

    async def test_a_licence_github_found_but_could_not_name_keeps_its_name(self) -> None:
        capture_body = capture(LINUX, "repos__torvalds__linux.json")["body"]
        self.assertEqual(capture_body["license"]["spdx_id"], "NOASSERTION")  # what GitHub sent
        result = await self.provider.analyze_repository(self.ref)
        s = result.snapshot
        self.assertEqual((s.license_spdx, s.license_name), (None, "Other"))
        self.assertEqual((s.stars, s.primary_language, s.default_branch, s.topics), (254120, "C", "master", ()))
        self.assertEqual(len(result.languages), 25)

    def test_the_analysis_route_says_other_with_no_identifier(self) -> None:
        response, body = get(make_app(github=self.provider), "/api/v1/github/repositories/torvalds/linux")
        self.assertEqual(response.status, 200, body)
        self.assertEqual(validate(body, COMPONENTS["RepositoryAnalysis"], COMPONENTS), [])
        self.assertEqual((body["license_spdx"], body["license_name"]), (None, "Other"))

    def test_a_403_and_a_404_each_cost_one_request_like_a_200(self) -> None:
        files = ("repos__torvalds__linux.json", "repos__torvalds__linux__languages.json", LIST_FILE, RELEASE_FILE_LINUX)
        used = [int(capture(LINUX, file)["headers"]["x-ratelimit-used"]) for file in files]
        self.assertEqual(used, [7, 8, 9, 10])


class AnArchivedRepository(unittest.IsolatedAsyncioTestCase):
    async def test_the_archived_flag_and_the_old_push_are_read_as_captured(self) -> None:
        transport = FakeTransport({
            "/repos/atom/atom": raw(ATOM, "repos__atom__atom.json"),
            "/repos/atom/atom/languages": raw(ATOM, "repos__atom__atom__languages.json"),
        })  # fmt: skip
        result = await provider_for(transport, Clock()).analyze_repository(RepoRef("atom", "atom"))
        s = result.snapshot
        self.assertIs(s.archived, True)
        self.assertEqual((s.full_name, s.default_branch, s.license_spdx), ("atom/atom", "master", "MIT"))
        self.assertEqual((s.stars, s.open_issues), (60709, 992))
        self.assertEqual(s.pushed_at, datetime(2023, 1, 3, 10, 49, 48, tzinfo=UTC))
        self.assertEqual(len(result.languages), 8)


class ARenamedRepository(unittest.IsolatedAsyncioTestCase):
    async def test_a_real_301_is_reported_as_moved_and_is_not_followed(self) -> None:
        moved = capture(NODE, "repos__joyent__node.json")
        self.assertEqual(moved["status"], 301)
        self.assertEqual(moved["body"]["message"], "Moved Permanently")
        self.assertEqual(moved["body"]["url"], "https://api.github.com/repositories/211666")
        transport = FakeTransport({"/repos/joyent/node": raw(NODE, "repos__joyent__node.json")})
        with self.assertRaises(GitHubMovedError):
            await provider_for(transport, Clock()).analyze_repository(RepoRef("joyent", "node"))
        self.assertEqual(transport.paths(), ["/repos/joyent/node"])  # the new address was not requested

    def test_the_route_says_not_found_with_the_moved_message_and_never_the_new_address(self) -> None:
        transport = FakeTransport({"/repos/joyent/node": raw(NODE, "repos__joyent__node.json")})
        app = make_app(github=provider_for(transport, Clock()))
        response, body = get(app, "/api/v1/github/repositories/joyent/node")
        self.assertEqual(response.status, 404)
        self.assertEqual(body["error"]["code"], "not_found")
        self.assertIn("renamed or moved", body["error"]["message"])
        self.assertNotIn("211666", json.dumps(body))
        self.assertEqual(validate(body, COMPONENTS["Error"], COMPONENTS), [])


class AMissingRepository(unittest.IsolatedAsyncioTestCase):
    async def test_a_real_404_is_not_found_and_the_second_request_is_never_made(self) -> None:
        missing = capture(FLASK, MISSING)
        self.assertEqual((missing["status"], missing["body"]["message"]), (404, "Not Found"))
        self.assertEqual(missing["body"]["status"], "404")  # GitHub puts the status in the body as a string
        name = "/repos/vyron-check-owner-that-does-not-exist/no-such-repository"
        transport = FakeTransport({name: raw(FLASK, MISSING)})
        with self.assertRaises(GitHubNotFoundError):
            await provider_for(transport, Clock()).analyze_repository(
                RepoRef("vyron-check-owner-that-does-not-exist", "no-such-repository")
            )
        self.assertEqual(transport.paths(), [name])


class TheQuota(unittest.TestCase):
    def test_the_real_rate_limit_payload_is_read(self) -> None:
        state = normalize_rate_limit(capture(FLASK, "rate_limit.json")["body"])
        self.assertEqual((state.limit, state.used, state.remaining, state.resource), (60, 18, 42, "core"))
        self.assertEqual(state.reset_epoch, 1793003166)

    def test_the_real_payload_has_resources_vyron_ignores(self) -> None:
        resources = capture(FLASK, "rate_limit.json")["body"]["resources"]
        self.assertTrue({"core", "search", "graphql"} <= set(resources))  # only 'core' is read


FLASK_STATS = "/repos/pallets/flask/stats/commit_activity"
LINUX_STATS = "/repos/torvalds/linux/stats/commit_activity"
FLASK_ATTEMPTS = tuple(f"repos__pallets__flask__stats__commit_activity__attempt-{n}.json" for n in (1, 2, 3, 4))
LINUX_ATTEMPT = "repos__torvalds__linux__stats__commit_activity__attempt-1.json"


class TheCommitActivityGitHubReallySent(unittest.IsolatedAsyncioTestCase):
    """What GitHub answered on 2026-11-04 to ``/stats/commit_activity``, and VYRON reading it as captured.

    ``pallets/flask``: three 202s (0.7, 5.8 and 11.0 seconds in), then a 200 on the fourth ask at 16.1 seconds.
    ``torvalds/linux``: a 200 on the first ask. The figures are "as captured", not current.
    """

    def setUp(self) -> None:
        self.clock = Clock()
        self.flask = RepoRef("pallets", "flask")

    def flask_script(self) -> Mapping[str, Any]:
        return {
            "/repos/pallets/flask": raw(STATS, "repos__pallets__flask.json"),
            FLASK_STATS: [raw(STATS, name) for name in FLASK_ATTEMPTS],
        }

    def test_what_githubs_computing_answer_and_its_year_really_look_like(self) -> None:
        computing = [capture(STATS, name) for name in FLASK_ATTEMPTS[:3]]
        for answer in computing:
            self.assertEqual((answer["status"], answer["body"]), (202, {}))  # an empty object, not an empty body
            self.assertNotIn("etag", answer["headers"])  # nothing to revalidate with
            self.assertNotIn("retry-after", answer["headers"])  # GitHub does not say when to come back
        self.assertEqual([a["seconds_since_first_attempt"] for a in computing], [0.7, 5.8, 11.0])
        ready = capture(STATS, FLASK_ATTEMPTS[3])
        self.assertEqual((ready["status"], ready["seconds_since_first_attempt"]), (200, 16.1))
        self.assertEqual(ready["headers"]["cache-control"], "public, max-age=60, s-maxage=60")
        self.assertTrue(ready["headers"]["etag"].startswith('W/"'))
        self.assertNotIn("last-modified", ready["headers"])  # the ETag is the only validator on this answer

    def test_the_year_has_the_shape_the_normaliser_holds_it_to(self) -> None:
        for name in (FLASK_ATTEMPTS[3], LINUX_ATTEMPT):
            body = capture(STATS, name)["body"]
            with self.subTest(name):
                self.assertEqual(len(body), 52)
                self.assertTrue(all(set(week) == {"days", "total", "week"} for week in body))
                self.assertTrue(all(len(week["days"]) == 7 and week["total"] == sum(week["days"]) for week in body))
                self.assertEqual({b["week"] - a["week"] for a, b in itertools.pairwise(body)}, {604_800})
                self.assertTrue(all(week["week"] % 86_400 == 0 for week in body))  # midnight UTC, every one
        self.assertEqual(capture(STATS, LINUX_ATTEMPT)["headers"]["cache-control"], "public, max-age=60, s-maxage=60")

    async def test_flask_is_computing_three_times_and_then_ready_without_the_provider_ever_waiting(self) -> None:
        waits: list[float] = []

        async def record(seconds: float) -> None:
            waits.append(seconds)

        transport = FakeTransport(self.flask_script())
        provider = LiveGitHubProvider(transport, InMemoryTTLCache(), clock=self.clock, sleep=record, jitter=lambda: 0.0)
        states = []
        for _ in range(4):
            states.append(await provider.commit_activity(self.flask))
            self.clock.now += 5.4  # about as far apart as the real asks were
        expected = [CommitActivityStatus.COMPUTING] * 3 + [CommitActivityStatus.READY]
        self.assertEqual([r.status for r in states], expected)
        self.assertEqual([len(r.weeks) for r in states], [0, 0, 0, 52])
        self.assertEqual(sum(week.total for week in states[3].weeks), 60)  # flask's year, as captured
        self.assertEqual(transport.paths().count(FLASK_STATS), 4)  # each ask reached GitHub: a 202 is never kept
        self.assertEqual(transport.paths().count("/repos/pallets/flask"), 1)  # the repository was reused
        self.assertEqual(waits, [])

    async def test_linux_is_ready_on_the_first_ask_and_the_year_ends_in_a_week_still_in_progress(self) -> None:
        transport = FakeTransport({
            "/repos/torvalds/linux": raw(STATS, "repos__torvalds__linux.json"),
            LINUX_STATS: raw(STATS, LINUX_ATTEMPT),
        })  # fmt: skip
        result = await provider_for(transport, self.clock).commit_activity(RepoRef("torvalds", "linux"))
        self.assertIs(result.status, CommitActivityStatus.READY)
        self.assertEqual(sum(week.total for week in result.weeks), 76_597)
        self.assertEqual(result.weeks[0].week_start, datetime(2025, 11, 9, tzinfo=UTC))
        self.assertEqual(result.weeks[-1].week_start, datetime(2026, 11, 1, tzinfo=UTC))
        # Captured on a Wednesday, so that week was unfinished: Sunday to Tuesday only.
        self.assertEqual(result.weeks[-1].days, (17, 5, 4, 0, 0, 0, 0))
        self.assertIs(result.cache_status, CacheStatus.FRESH)
        self.assertEqual(transport.paths(), ["/repos/torvalds/linux", LINUX_STATS])

    async def test_the_etag_is_sent_back_exactly_as_github_wrote_it(self) -> None:
        etag = capture(STATS, LINUX_ATTEMPT)["headers"]["etag"]
        quota = {"x-ratelimit-remaining": "40"}
        transport = FakeTransport({
            "/repos/torvalds/linux": [raw(STATS, "repos__torvalds__linux.json"), RawResponse(304, quota, b"")],
            LINUX_STATS: [raw(STATS, LINUX_ATTEMPT), RawResponse(304, quota, b"")],
        })  # fmt: skip
        provider = provider_for(transport, self.clock)
        await provider.commit_activity(RepoRef("torvalds", "linux"))
        self.clock.now += 120
        again = await provider.commit_activity(RepoRef("torvalds", "linux"))
        self.assertIs(again.cache_status, CacheStatus.REVALIDATED)
        self.assertEqual(transport.calls[-1], (LINUX_STATS, {"if-none-match": etag}))
        self.assertEqual(sum(week.total for week in again.weeks), 76_597)

    def test_the_route_answers_in_its_schema_from_the_real_payloads_in_both_states(self) -> None:
        computing = provider_for(FakeTransport(self.flask_script()), self.clock)
        response, body = get(make_app(github=computing), "/api/v1/github/repositories/pallets/flask/commit-activity")
        self.assertEqual(response.status, 200, body)
        self.assertEqual(validate(body, COMPONENTS["CommitActivity"], COMPONENTS), [])
        self.assertEqual((body["status"], body["weeks"]), ("computing", []))
        script = {
            "/repos/torvalds/linux": raw(STATS, "repos__torvalds__linux.json"),
            LINUX_STATS: raw(STATS, LINUX_ATTEMPT),
        }
        ready = provider_for(FakeTransport(script), self.clock)
        response, body = get(make_app(github=ready), "/api/v1/github/repositories/torvalds/linux/commit-activity")
        self.assertEqual(response.status, 200, body)
        self.assertEqual(validate(body, COMPONENTS["CommitActivity"], COMPONENTS), [])
        self.assertEqual((body["status"], len(body["weeks"])), ("ready", 52))
        last_week = {"week_start": "2026-11-01T00:00:00Z", "days": [17, 5, 4, 0, 0, 0, 0], "total": 26}
        self.assertEqual(body["weeks"][-1], last_week)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_every_ask_cost_one_request_a_202_included(self) -> None:
        used = [int(capture(STATS, name)["headers"]["x-ratelimit-used"]) for name in (*FLASK_ATTEMPTS, LINUX_ATTEMPT)]
        self.assertEqual(used, [11, 12, 13, 14, 15])  # one more each time, the three 202s as much as the 200s

    def test_the_captured_year_reads_the_same_through_the_normaliser_alone(self) -> None:
        weeks = normalize_commit_activity(capture(STATS, FLASK_ATTEMPTS[3])["body"])
        self.assertEqual(len(weeks), 52)
        self.assertEqual(weeks[-1].total, 0)  # flask's current week had no commits yet when it was captured


if __name__ == "__main__":
    unittest.main()
