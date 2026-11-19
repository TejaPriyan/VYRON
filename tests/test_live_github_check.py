"""scripts/live_github_check.py, run against the local stand-in (fixture data, never GitHub).

These tests show the script itself works: it passes when the path is healthy, fails when it is not, says it could
not run when nothing answers, never sends or prints a token, saves captures without credentials, and labels a
stand-in pass as exactly that. They cannot show that real GitHub passes; only running the script on a machine
that can reach GitHub does, and until someone has, the live path is UNVERIFIED.
"""

import _paths  # noqa: F401

import asyncio
import copy
import io
import json
import socket
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import live_github_check as check
from github_fakes import FIXTURE_REF, FakeGitHubProvider, FakeTransport, commit_activity_payload, error, ok
from standin_github import start_standin
from vyron_api.api import serializers
from vyron_api.github.transport import UrllibGitHubTransport
from vyron_api.github import conditional as conditional_module
from vyron_api.github import provider as provider_module
from vyron_api.providers.github import CacheStatus

# Shorter than the repository scanner's minimum, so it is not mistaken for a real credential.
SENTINEL_TOKEN = "ghp_SentinelTokenNotReal000000000001"


def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class LiveCheckTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.standin = start_standin()
        self.addCleanup(self._stop)

    def _stop(self) -> None:
        self.standin.shutdown()
        self.standin.server_close()

    def run_check(self, *args: str, environ: dict[str, str] | None = None) -> tuple[int, str]:
        out = io.StringIO()
        code = check.main(["--base-url", self.standin.base, "--timeout", "5", *args], environ or {}, out)
        return code, out.getvalue()


class AgainstTheStandIn(LiveCheckTestCase):
    def test_a_healthy_repository_passes_and_is_labelled_as_a_stand_in(self) -> None:
        code, output = self.run_check()
        self.assertEqual(code, 0, output)
        self.assertIn("STAND-IN, FIXTURE DATA", output)
        self.assertIn("does NOT verify api.github.com", output)
        self.assertIn("UNVERIFIED", output)
        self.assertNotIn("VERIFIED for what was checked", output)
        self.assertNotIn("FAIL", output)

    def test_it_makes_the_requests_it_says_it_does_and_the_second_read_is_not_one_of_them(self) -> None:
        code, _ = self.run_check()
        self.assertEqual(code, 0)
        core = [e["path"] for e in self.standin.log_copy() if e["path"] != "/rate_limit"]
        self.assertEqual(
            core,
            ["/repos/fixture-org/example-repo", "/repos/fixture-org/example-repo/languages",
             "/repos/fixture-org/missing-repo"],
        )  # fmt: skip

    def test_a_broken_repository_fails_the_run_and_says_what_went_wrong(self) -> None:
        code, output = self.run_check("--repo", "fixture-org/broken-repo")
        self.assertEqual(code, 1, output)
        self.assertIn("FAIL  the analysis route answers 200", output)
        self.assertIn("upstream_error", output)
        self.assertIn("RESULT: FAIL", output)

    def test_a_private_repository_is_not_found_so_it_fails_the_positive_check(self) -> None:
        code, output = self.run_check("--repo", "fixture-org/private-repo")
        self.assertEqual(code, 1, output)
        self.assertIn("not_found", output)

    def test_a_name_that_does_exist_is_skipped_for_the_404_check_not_failed(self) -> None:
        code, output = self.run_check("--missing", "fixture-org/example-repo")
        self.assertEqual(code, 0, output)
        self.assertIn("SKIP  a repository that does not exist answers 404", output)

    def test_several_repositories_are_each_read(self) -> None:
        code, output = self.run_check("--repo", "fixture-org/example-repo", "--repo", "fixture-org/quiet-repo")
        self.assertEqual(code, 0, output)
        self.assertIn("fixture-org/quiet-repo: ", output)

    def test_a_rate_limited_server_says_so_and_does_not_pretend_it_ran(self) -> None:
        code, output = self.run_check("--repo", "fixture-org/limited-repo")
        self.assertEqual(code, 1, output)
        self.assertIn("upstream_rate_limited", output)
        self.assertIn("rate limit is used up", output)


class ActivityAgainstTheStandIn(LiveCheckTestCase):
    def core_paths(self) -> list[str]:
        return [e["path"] for e in self.standin.log_copy() if e["path"] != "/rate_limit"]

    def test_the_opt_in_read_passes_and_is_still_labelled_a_stand_in(self) -> None:
        code, output = self.run_check("--activity")
        self.assertEqual(code, 0, output)
        self.assertIn("contributors and latest release", output)
        self.assertIn("contributors listed: fixture-person-a (120)", output)
        self.assertIn("latest release v1.2.3", output)
        self.assertIn("PASS  the read cost two or three of GitHub's requests", output)
        self.assertIn("PASS  the second read is served from VYRON's cache", output)
        self.assertIn("UNVERIFIED", output)
        self.assertNotIn("FAIL", output)

    def test_it_asks_only_for_what_it_says_and_the_second_read_is_from_the_cache(self) -> None:
        code, _ = self.run_check("--activity")
        self.assertEqual(code, 0)
        base = "/repos/fixture-org/example-repo"
        self.assertEqual(
            self.core_paths(),
            [base, f"{base}/languages", f"{base}/contributors", f"{base}/releases/latest",
             "/repos/fixture-org/missing-repo", "/repos/fixture-org/missing-repo"],
        )  # fmt: skip

    def test_without_the_flag_nothing_extra_is_asked(self) -> None:
        self.run_check()
        self.assertFalse([p for p in self.core_paths() if "contributors" in p or "releases" in p])

    def test_an_empty_repository_passes_and_the_cache_check_is_skipped_with_the_reason(self) -> None:
        code, output = self.run_check("--activity", "--repo", "fixture-org/quiet-repo")
        self.assertEqual(code, 0, output)
        self.assertIn("contributors none: no contributors; no published release", output)
        self.assertIn("SKIP  a second read is served from VYRON's cache", output)
        self.assertIn("never stores those", output)

    def test_a_refused_list_and_a_missing_release_are_valid_answers_not_failures(self) -> None:
        for name, expected in (("huge-repo", "contributors declined"), ("unreleased-repo", "no published release")):
            with self.subTest(name):
                code, output = self.run_check("--activity", "--repo", f"fixture-org/{name}")
                self.assertEqual(code, 0, output)
                self.assertIn(expected, output)

    def test_a_rate_limited_or_garbled_list_fails_the_run(self) -> None:
        for name, code_word in (("burst-repo", "upstream_rate_limited"), ("garbled-list-repo", "upstream_error")):
            with self.subTest(name):
                code, output = self.run_check("--activity", "--repo", f"fixture-org/{name}")
                self.assertEqual(code, 1, output)
                self.assertIn("FAIL  the activity route answers 200", output)
                self.assertIn(code_word, output)

    def test_a_moved_repository_gets_a_hint_that_says_what_to_do(self) -> None:
        code, output = self.run_check("--repo", "fixture-org/moved-repo")
        self.assertEqual(code, 1, output)
        self.assertIn("renamed or moved", output)
        self.assertIn("Open the repository on github.com to see its current name", output)

    def test_the_capture_holds_the_two_new_endpoints_under_file_names_windows_accepts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, output = self.run_check("--activity", "--capture", tmp)
            files = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in Path(tmp).iterdir()}
        self.assertEqual(code, 0, output)
        manifest = files.pop("MANIFEST.json")
        self.assertEqual(sorted(files), sorted(manifest["files"]))
        self.assertIn("repos__fixture-org__example-repo__contributors__per_page-10.json", files)
        self.assertIn("repos__fixture-org__example-repo__releases__latest.json", files)
        for name in files:
            self.assertFalse(set(name) & set('?=<>:"|*\\'), name)
        contributors = files["repos__fixture-org__example-repo__contributors__per_page-10.json"]
        self.assertEqual(contributors["request"], "/repos/fixture-org/example-repo/contributors?per_page=10")
        self.assertEqual(contributors["status"], 200)
        self.assertNotIn("contributors", "".join(n for n in files if "missing" in n))  # nothing for the missing name

    def test_a_204_is_captured_as_an_empty_body_with_its_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, output = self.run_check("--activity", "--repo", "fixture-org/quiet-repo", "--capture", tmp)
            first = Path(tmp) / "repos__fixture-org__quiet-repo__contributors__per_page-10.json"
            second = Path(tmp) / "repos__fixture-org__quiet-repo__releases__latest.json"
            record = json.loads(first.read_text(encoding="utf-8"))
            release = json.loads(second.read_text(encoding="utf-8"))
        self.assertEqual(code, 0, output)
        self.assertEqual((record["status"], record["body_text"]), (204, ""))
        self.assertEqual(release["status"], 404)

    def test_the_capture_without_the_flag_has_no_new_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.run_check("--capture", tmp)
            names = sorted(p.name for p in Path(tmp).iterdir())
        self.assertFalse([n for n in names if "contributors" in n or "releases" in n])


class FakeApi:
    """Answers the activity check from a script, so a wrong answer can be put in front of its checks."""

    def __init__(self, body: dict[str, Any], quota: list[int | None], second: dict[str, Any] | None = None) -> None:
        self.body, self.second, self.quota = body, second if second is not None else body, list(quota)
        self.activity_calls = 0

    def get(self, path: str) -> tuple[int, Any]:
        if path.endswith("/rate-limit"):
            remaining = self.quota.pop(0) if len(self.quota) > 1 else self.quota[0]
            return 200, {"limit": 60, "remaining": remaining, "used": None, "reset_at": None, "resource": "core"}
        self.activity_calls += 1
        return 200, self.body if self.activity_calls == 1 else self.second


def good_body() -> dict[str, Any]:
    result = asyncio.run(FakeGitHubProvider().repository_activity(FIXTURE_REF))
    body = serializers.repository_activity(result)
    base = "https://api.github.com"
    body["source_urls"] = [f"{base}{FIXTURE_REF.api_path}", f"{base}{FIXTURE_REF.api_path}/contributors?per_page=10",
                           f"{base}{FIXTURE_REF.api_path}/releases/latest"]  # fmt: skip
    body["fetched_at"] = check.datetime.now(check.timezone.utc).isoformat().replace("+00:00", "Z")
    return body


class TheActivityChecksCatchWrongAnswers(unittest.TestCase):
    def run_check(self, body: dict[str, Any], quota: list[int | None], second: dict[str, Any] | None = None) -> str:
        report = check.Report(io.StringIO())
        api = FakeApi(body, quota, second)
        check.check_activity(report, api, "https://api.github.com", FIXTURE_REF.full_name)  # type: ignore[arg-type]
        return report.out.getvalue()  # type: ignore[attr-defined]

    def test_a_good_answer_passes_every_check(self) -> None:
        output = self.run_check(good_body(), [50, 48, 48])
        self.assertNotIn("FAIL", output)
        self.assertEqual(output.count("PASS"), 14, output)

    def test_each_wrong_answer_fails_the_check_that_is_meant_to_catch_it(self) -> None:
        def broken(change: Any) -> dict[str, Any]:
            body = copy.deepcopy(good_body())
            change(body)
            return body

        cases = {
            "a profile link that is not github.com/<login>": (
                lambda b: b["contributors"][0].update(profile_url="https://evil.example/x"),
                "FAIL  profile links are github.com/<login>",
            ),
            "a bot with a profile link": (
                lambda b: b["contributors"][1].update(is_bot=True, profile_url="https://github.com/fixture-person-b"),
                "FAIL  profile links are github.com/<login>",
            ),
            "contributors out of order": (
                lambda b: b["contributors"].reverse(),
                "FAIL  the contributor list agrees with its status",
            ),
            "a list with the status 'none'": (
                lambda b: b.update(contributors_status="none"),
                "FAIL  the contributor list agrees with its status",
            ),
            "a release link on another site": (
                lambda b: b["latest_release"].update(html_url="https://evil.example/releases/1"),
                "FAIL  the release link is on this repository's releases page",
            ),
            "a release from the future": (
                lambda b: b["latest_release"].update(published_at="2999-01-01T00:00:00Z"),
                "FAIL  the release was not published in the future",
            ),
            "evidence from another host": (
                lambda b: b["source_urls"].__setitem__(0, "https://evil.example/repos/x"),
                "FAIL  the evidence is three GitHub API URLs",
            ),
            "a stale 'fetched_at'": (
                lambda b: b.update(fetched_at="2020-01-01T00:00:00Z"),
                "FAIL  'fetched_at' is within five minutes of now",
            ),
            "a provenance that is not sourced": (
                lambda b: b.update(provenance="seed"),
                "FAIL  it is labelled 'sourced'",
            ),
            "another repository": (
                lambda b: b.update(full_name="someone/else"),
                "FAIL  GitHub's repository is the one asked for",
            ),
        }
        for label, (change, expected) in cases.items():
            with self.subTest(label):
                self.assertIn(expected, self.run_check(broken(change), [50, 48, 48]))

    def test_a_cost_that_is_not_two_or_three_fails_and_two_or_three_pass(self) -> None:
        self.assertIn("FAIL  the read cost two or three", self.run_check(good_body(), [50, 45, 45]))
        self.assertIn("FAIL  the read cost two or three", self.run_check(good_body(), [50, 50, 50]))
        for used in (2, 3):
            self.assertIn("PASS  the read cost two or three", self.run_check(good_body(), [50, 50 - used, 50 - used]))

    def test_no_reported_quota_skips_the_cost_check_instead_of_guessing(self) -> None:
        output = self.run_check(good_body(), [None])
        self.assertIn("SKIP  the read cost two or three of GitHub's requests  (GitHub did not report a quota)", output)

    def test_a_second_read_that_is_not_the_cached_one_fails(self) -> None:
        later = good_body()
        later["fetched_at"] = "2999-01-01T00:00:00Z"
        output = self.run_check(good_body(), [50, 48, 48], later)
        self.assertIn("FAIL  the second read is served from VYRON's cache", output)

    def test_a_second_read_that_spends_quota_fails(self) -> None:
        output = self.run_check(good_body(), [50, 48, 47])
        self.assertIn("FAIL  the second read did not use any of GitHub's quota", output)


class FlowAgainstTheStandIn(LiveCheckTestCase):
    """``--flow`` replays the page's sequence. The waits are simulated here (a stand-in only); GitHub's are real."""

    def run_flow(self, *args: str) -> tuple[int, str]:
        return self.run_check("--flow", "--simulate-wait", *args)

    def test_it_passes_says_what_it_did_and_is_still_labelled_a_stand_in(self) -> None:
        code, output = self.run_flow()
        self.assertEqual(code, 0, output)
        self.assertIn("simulated: VYRON's clock was moved on; a stand-in only", output)
        self.assertIn("PASS  step 1: the label 'fresh' is the one GitHub's answers justify ('fresh')", output)
        self.assertIn("PASS  step 2: the label is 'fresh' (the page says 'Live')", output)
        self.assertIn(
            "PASS  step 3: the label 'revalidated' is the one GitHub's answers justify ('revalidated')", output
        )
        self.assertIn("does NOT verify api.github.com", output)
        self.assertNotIn("FAIL", output)

    def test_it_sends_exactly_the_requests_it_lists(self) -> None:
        code, output = self.run_flow()
        self.assertEqual(code, 0, output)
        base = "/repos/fixture-org/example-repo"
        contributors, release = f"{base}/contributors", f"{base}/releases/latest"
        core = [
            (e["path"], e["if_none_match"] is not None) for e in self.standin.log_copy() if e["path"] != "/rate_limit"
        ]
        self.assertEqual(
            core,
            [(base, False), (f"{base}/languages", False),
             ("/repos/fixture-org/missing-repo", False),  # the checks that run first
             (base, False), (f"{base}/languages", False),  # step 1: a fresh cache, so everything is asked outright
             (base, True), (contributors, False), (release, False),  # step 2: only the old copy is re-asked
             (base, True), (contributors, True), (release, True)],  # step 3: everything held is re-asked
        )  # fmt: skip
        listed = [line.strip() for line in output.splitlines() if line.strip().startswith("GET ")]
        self.assertEqual(len(listed), 8, output)

    def test_without_the_flag_the_flow_does_not_run(self) -> None:
        code, output = self.run_check()
        self.assertEqual(code, 0, output)
        self.assertNotIn("page's own sequence", output)

    def test_a_refused_contributor_list_and_a_missing_release_keep_the_label_fresh_each_time(self) -> None:
        code, output = self.run_flow("--repo", "fixture-org/huge-repo")
        self.assertEqual(code, 0, output)
        self.assertIn("->  403", output)
        self.assertNotIn("'revalidated'", output)  # a refused answer is never stored, so it is never "unchanged"
        self.assertIn("PASS  step 3: the label 'fresh' is the one GitHub's answers justify ('fresh')", output)

    def test_an_empty_repository_is_fresh_each_time_too(self) -> None:
        code, output = self.run_flow("--repo", "fixture-org/quiet-repo")
        self.assertEqual(code, 0, output)
        self.assertIn("->  204", output)
        self.assertNotIn("'revalidated'", output)

    def test_it_would_have_caught_the_label_the_first_real_run_showed(self) -> None:
        def old_rule(reads: Any, *, uncached_answers: int = 0) -> CacheStatus:
            """What the provider did before: the least current part decides, so one 304 made it 'Confirmed'."""
            if any(read.stale for read in reads):
                return CacheStatus.STALE
            return CacheStatus.REVALIDATED if any(read.revalidated for read in reads) else CacheStatus.FRESH

        with mock.patch.object(provider_module, "_overall_status", old_rule):
            code, output = self.run_flow()
        self.assertEqual(code, 1, output)
        self.assertIn("FAIL  step 2: the label is 'fresh'", output)
        self.assertIn("FAIL  step 2: the label 'revalidated' is the one GitHub's answers justify ('fresh')", output)
        self.assertIn("RESULT: FAIL", output)

    def test_a_label_that_claims_too_little_is_caught_as_well(self) -> None:
        def never_confirms(reads: Any, *, uncached_answers: int = 0) -> CacheStatus:
            return CacheStatus.FRESH

        with mock.patch.object(provider_module, "_overall_status", never_confirms):
            code, output = self.run_flow()
        self.assertEqual(code, 1, output)
        self.assertIn("FAIL  step 3: the label 'fresh' is the one GitHub's answers justify ('revalidated')", output)

    def test_a_provider_that_downloads_again_instead_of_asking_is_caught(self) -> None:
        with mock.patch.object(conditional_module, "conditional_headers", lambda entry: {}):
            code, output = self.run_flow()
        self.assertEqual(code, 1, output)
        self.assertIn("FAIL  step 2: the repository copy was confirmed with a conditional request", output)
        self.assertIn("FAIL  step 3: every copy VYRON held was confirmed with a conditional request", output)

    def test_a_broken_repository_stops_the_flow_at_step_one_with_a_hint(self) -> None:
        code, output = self.run_flow("--repo", "fixture-org/broken-repo")
        self.assertEqual(code, 1, output)
        self.assertIn("FAIL  step 1: the analysis route answers 200", output)
        self.assertNotIn("step 2", output)
        self.assertNotIn("Traceback", output)

    def test_a_wait_that_is_not_longer_than_the_fresh_window_is_refused_before_any_request(self) -> None:
        code, output = self.run_flow("--flow-wait", "60")
        self.assertEqual(code, 2, output)
        self.assertIn("--flow-wait must be over 60 seconds", output)
        self.assertEqual(self.standin.log_copy(), [])

    def test_only_the_first_repository_is_replayed(self) -> None:
        code, output = self.run_flow("--repo", "fixture-org/example-repo", "--repo", "fixture-org/quiet-repo")
        self.assertEqual(code, 0, output)
        self.assertEqual(output.count("the page's own sequence"), 1)
        self.assertIn("fixture-org/example-repo: the page's own sequence", output)


class TheFlowNeverSimulatesGitHub(unittest.TestCase):
    def test_simulated_waits_are_refused_against_the_real_github_before_any_request(self) -> None:
        out = io.StringIO()
        self.assertEqual(check.main(["--flow", "--simulate-wait", "--timeout", "1"], {}, out), 2)
        self.assertIn("--simulate-wait is only for a local stand-in", out.getvalue())
        self.assertNotIn("Reaching GitHub", out.getvalue())

    def test_a_real_wait_really_sleeps_and_shows_the_line_first(self) -> None:
        out = io.StringIO()
        flushed: list[bool] = []
        out.flush = lambda: flushed.append(True)  # type: ignore[method-assign]
        clock = check.FlowClock(simulated=False)
        with mock.patch.object(check.time, "sleep") as sleep:
            clock.wait(65, out)
        sleep.assert_called_once_with(65)
        self.assertEqual(flushed, [True])

    def test_a_simulated_wait_moves_the_clock_on_without_sleeping(self) -> None:
        clock = check.FlowClock(simulated=True)
        before = clock.now()
        with mock.patch.object(check.time, "sleep") as sleep:
            clock.wait(65, io.StringIO())
        sleep.assert_not_called()
        self.assertGreaterEqual(clock.now() - before, 65)
        self.assertLess(clock.now() - before, 66)


class TheRecorder(unittest.TestCase):
    def test_it_notes_each_request_and_changes_nothing_about_it(self) -> None:
        inner = FakeTransport({"/a": ok({"x": 1}), "/b": error(404, "Not Found")})
        recorder = check.RecordingTransport(inner)  # type: ignore[arg-type]
        first = asyncio.run(recorder.get("/a", {"If-None-Match": '"e"'}))
        second = asyncio.run(recorder.get("/a", {"if-modified-since": "Mon, 02 Nov 2026 03:46:00 GMT"}))
        third = asyncio.run(recorder.get("/b"))
        self.assertEqual((first.status, third.status), (200, 404))
        self.assertIs(second, inner.script["/a"][0])
        self.assertEqual(
            recorder.log,
            [check.Exchange("/a", True, 200, "59"), check.Exchange("/a", True, 200, "59"),
             check.Exchange("/b", False, 404, None)],
        )  # fmt: skip
        self.assertEqual(inner.calls[0], ("/a", {"If-None-Match": '"e"'}))  # what was sent is what was passed in
        self.assertEqual(recorder.url_for("/a"), "https://api.github.com/a")


class TheExpectedLabel(unittest.TestCase):
    @staticmethod
    def label(*answers: tuple[str, int]) -> str:
        return check.expected_label([check.Exchange(path, True, status, None) for path, status in answers])

    def test_unchanged_only_when_every_answer_was_304(self) -> None:
        self.assertEqual(self.label(("/a", 304), ("/b", 304), ("/c", 304)), "revalidated")

    def test_anything_else_is_fresh(self) -> None:
        for answers in (
            [("/a", 304), ("/b", 200)], [("/a", 200)], [("/a", 304), ("/b", 403), ("/c", 404)],
            [("/a", 304), ("/b", 204)], [],
        ):  # fmt: skip
            with self.subTest(answers=answers):
                self.assertEqual(self.label(*answers), "fresh")

    def test_a_retried_request_is_judged_by_its_last_answer(self) -> None:
        self.assertEqual(self.label(("/a", 502), ("/a", 304)), "revalidated")
        self.assertEqual(self.label(("/a", 304), ("/b", 502), ("/b", 200)), "fresh")


class StatsCaptureAgainstTheStandIn(LiveCheckTestCase):
    """``--capture DIR --stats``: ask for commit activity until GitHub stops saying 202, saving every attempt."""

    STATS = "/repos/fixture-org/example-repo/stats/commit_activity"

    def run_capture(self, *args: str) -> tuple[int, str, dict[str, Any]]:
        with tempfile.TemporaryDirectory() as tmp:
            code, output = self.run_check("--capture", tmp, "--stats", "--stats-wait", "0", *args)
            files = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in Path(tmp).iterdir()}
        return code, output, files

    def stats_asks(self) -> list[dict[str, Any]]:
        return [e for e in self.standin.log_copy() if e["path"].endswith("/stats/commit_activity")]

    def test_it_asks_again_while_the_answer_is_202_and_saves_every_attempt(self) -> None:
        code, output, files = self.run_capture()
        self.assertEqual(code, 0, output)
        attempts = sorted(n for n in files if "commit_activity" in n)
        stem = "repos__fixture-org__example-repo__stats__commit_activity__attempt-"
        self.assertEqual(attempts, [f"{stem}{i}.json" for i in (1, 2, 3)])
        records = [files[n] for n in attempts]
        self.assertEqual([r["status"] for r in records], [202, 202, 200])
        self.assertEqual([r["attempt"] for r in records], [1, 2, 3])
        self.assertEqual(records[0]["body"], {})  # GitHub's real 202 body is the empty object {}, saved as that
        self.assertEqual(records[2]["body"], commit_activity_payload())
        self.assertIn("settled after 3 attempts", output)

    def test_each_attempt_has_the_usual_record_plus_its_number_and_the_time_since_the_first(self) -> None:
        _, _, files = self.run_capture()
        for name, record in files.items():
            if "commit_activity" not in name:
                continue
            with self.subTest(file=name):
                self.assertEqual(set(record) - {"body", "body_text"}, {
                    "request", "status", "headers", "attempt", "seconds_since_first_attempt"})
                self.assertEqual(record["request"], self.STATS)
                self.assertIsInstance(record["seconds_since_first_attempt"], float)
                self.assertGreaterEqual(record["seconds_since_first_attempt"], 0)
                self.assertLessEqual(set(record["headers"]), set(check.CAPTURE_HEADERS))
        self.assertEqual(files["MANIFEST.json"]["stats"], {
            "endpoint": "/stats/commit_activity", "repositories": ["fixture-org/example-repo"],
            "wait_seconds": 0.0, "max_attempts": 6})
        listed = set(files["MANIFEST.json"]["files"])
        self.assertTrue({n for n in files if "commit_activity" in n} <= listed)

    def test_every_ask_goes_to_the_stand_in_outright_and_without_credentials(self) -> None:
        self.run_capture()
        asks = self.stats_asks()
        self.assertEqual(len(asks), 3)
        self.assertEqual({a["path"] for a in asks}, {self.STATS})
        self.assertEqual({a["if_none_match"] for a in asks}, {None})  # never "has this changed?": a 202 is not cached
        self.assertEqual({a["authenticated"] for a in asks}, {False})

    def test_it_stops_at_the_first_answer_that_is_not_202(self) -> None:
        code, output, files = self.run_capture("--repo", "fixture-org/quiet-repo")
        self.assertEqual(code, 0, output)
        name = "repos__fixture-org__quiet-repo__stats__commit_activity__attempt-1.json"
        self.assertEqual((files[name]["status"], files[name]["body_text"]), (204, ""))
        self.assertFalse([n for n in files if "quiet-repo__stats" in n and not n.endswith("attempt-1.json")])
        self.assertIn("settled after 1 attempt", output)
        self.assertNotIn("1 attempts", output)

    def test_an_answer_that_never_settles_is_saved_as_it_came_and_said_so(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, output = self.run_check(
                "--capture", tmp, "--stats", "--stats-wait", "0", "--stats-attempts", "3",
                "--repo", "fixture-org/slow-stats-repo",
            )
            saved = sorted(p.name for p in Path(tmp).iterdir() if "commit_activity" in p.name)
        self.assertEqual(code, 0, output)
        self.assertEqual(len(saved), 3)
        self.assertIn("still 202 after 3 attempts", output)
        self.assertEqual(len(self.stats_asks()), 3)

    def test_it_asks_for_each_repository_but_never_for_the_name_that_does_not_exist(self) -> None:
        self.run_capture("--repo", "fixture-org/example-repo", "--repo", "fixture-org/quiet-repo")
        self.assertEqual(
            sorted({a["path"] for a in self.stats_asks()}),
            ["/repos/fixture-org/example-repo/stats/commit_activity",
             "/repos/fixture-org/quiet-repo/stats/commit_activity"],
        )  # fmt: skip

    def test_without_the_flag_nothing_about_commit_activity_is_asked_or_saved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, _ = self.run_check("--capture", tmp)
            names = [p.name for p in Path(tmp).iterdir()]
            manifest = json.loads((Path(tmp) / "MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertFalse([n for n in names if "stats" in n])
        self.assertNotIn("stats", manifest)
        self.assertEqual(self.stats_asks(), [])

    def test_the_file_names_are_legal_on_windows(self) -> None:
        _, _, files = self.run_capture()
        for name in files:
            self.assertFalse(set(name) & set('?=<>:"|*\\'), name)

    def test_stats_without_capture_and_out_of_range_numbers_are_refused_before_any_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:  # a regression must not leave a folder in the working directory
            cases = (
                (("--stats",), "needs --capture DIR"),
                (("--capture", tmp, "--stats", "--stats-wait", "61"), "--stats-wait must be 0 to 60"),
                (("--capture", tmp, "--stats", "--stats-wait", "-1"), "--stats-wait must be 0 to 60"),
                (("--capture", tmp, "--stats", "--stats-attempts", "0"), "--stats-attempts 1 to 10"),
                (("--capture", tmp, "--stats", "--stats-attempts", "11"), "--stats-attempts 1 to 10"),
            )
            for args, words in cases:
                with self.subTest(args=args[:1] + args[2:]):
                    code, output = self.run_check(*args)
                    self.assertEqual(code, 2, output)
                    self.assertIn(words, output)
            self.assertEqual(list(Path(tmp).iterdir()), [])
        self.assertEqual(self.standin.log_copy(), [])


class StatsCaptureUnits(LiveCheckTestCase):
    def transport(self) -> UrllibGitHubTransport:
        return UrllibGitHubTransport(base_url=self.standin.base, allow_local_test_server=True, timeout=5)

    def test_it_waits_between_asks_and_not_before_the_first(self) -> None:
        sleeps: list[float] = []
        report = check.Report(io.StringIO())
        with tempfile.TemporaryDirectory() as tmp:
            written = check.capture_stats(
                Path(tmp), self.transport(), ["fixture-org/example-repo"], report,
                wait_seconds=0.5, attempts=6, sleep=sleeps.append,
            )  # fmt: skip
        self.assertEqual(len(written), 3)
        self.assertEqual(sleeps, [0.5, 0.5])

    def test_a_failure_to_reach_github_is_said_and_saves_nothing_for_that_ask(self) -> None:
        class Down:
            async def get(self, path: str, extra_headers: Any = None) -> Any:
                raise ConnectionError("unreachable")

        report = check.Report(io.StringIO())
        with tempfile.TemporaryDirectory() as tmp:
            written = check.capture_stats(
                Path(tmp), Down(), ["fixture-org/example-repo"], report,  # type: ignore[arg-type]
                wait_seconds=0, attempts=3, sleep=lambda _: None,
            )  # fmt: skip
            saved = list(Path(tmp).iterdir())
        self.assertEqual((written, saved), ([], []))
        self.assertIn("could not capture", report.out.getvalue())  # type: ignore[attr-defined]
        self.assertNotIn("still 202", report.out.getvalue())  # type: ignore[attr-defined]

    def test_the_header_allow_list_holds_nothing_that_could_identify_the_caller(self) -> None:
        for name in ("authorization", "cookie", "set-cookie", "x-github-request-id", "x-github-sso"):
            self.assertNotIn(name, check.CAPTURE_HEADERS)
        self.assertIn("cache-control", check.CAPTURE_HEADERS)


class TheLicenceWording(unittest.TestCase):
    def test_it_says_what_github_reported_and_nothing_more(self) -> None:
        body = {
            "full_name": "o/r", "stars": 1, "forks": 2, "open_issues": 3, "default_branch": "main", "languages": {},
            "pushed_at": None, "license_spdx": None, "license_name": None,
        }  # fmt: skip
        self.assertIn("license none identified by GitHub,", check.summarize(body))
        self.assertNotIn("declared", check.summarize(body))
        self.assertIn("license Other (no standard identifier),", check.summarize({**body, "license_name": "Other"}))
        named = {**body, "license_spdx": "MIT", "license_name": "MIT License"}
        self.assertIn("license MIT,", check.summarize(named))


class WhenItCannotRun(unittest.TestCase):
    def test_nothing_listening_is_exit_two_with_a_hint_and_no_traceback(self) -> None:
        out = io.StringIO()
        code = check.main(["--base-url", f"http://127.0.0.1:{closed_port()}", "--timeout", "2"], {}, out)
        self.assertEqual(code, 2)
        text = out.getvalue()
        self.assertIn("could not run", text)
        self.assertIn("Nothing was checked", text)
        self.assertNotIn("Traceback", text)
        self.assertNotIn("PASS", text)

    def test_a_non_loopback_base_url_is_refused_before_any_request(self) -> None:
        for url in ("https://example.com", "http://192.0.2.1:9", "http://localhost.evil.test"):
            with self.subTest(url=url):
                out = io.StringIO()
                self.assertEqual(check.main(["--base-url", url], {}, out), 2)
                self.assertIn("only for a local stand-in", out.getvalue())

    def test_a_malformed_repository_name_is_exit_two(self) -> None:
        for bad in ("flask", "a/b/c", "/x", "x/"):
            with self.subTest(repo=bad):
                out = io.StringIO()
                self.assertEqual(check.main(["--repo", bad, "--base-url", "http://127.0.0.1:9"], {}, out), 2)
                self.assertIn("not owner/name", out.getvalue())


class TheToken(LiveCheckTestCase):
    def test_a_token_is_never_printed_and_never_sent_to_a_stand_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, output = self.run_check("--capture", tmp, environ={"GITHUB_TOKEN": SENTINEL_TOKEN})
            saved = "".join(p.read_text(encoding="utf-8") for p in Path(tmp).iterdir())
        self.assertEqual(code, 0, output)
        self.assertNotIn(SENTINEL_TOKEN, output)
        self.assertNotIn(SENTINEL_TOKEN, saved)
        self.assertIn("not sent to a stand-in", output)
        self.assertEqual({e["authenticated"] for e in self.standin.log_copy()}, {False})

    def test_an_unusable_token_is_refused_without_echoing_it(self) -> None:
        # Only a real target ever uses the token; the check on its shape runs before any request is made.
        out = io.StringIO()
        bad = "not a token\nwith a newline"
        self.assertEqual(check.main(["--timeout", "1"], {"GITHUB_TOKEN": bad}, out), 2)
        self.assertNotIn("with a newline", out.getvalue())
        self.assertIn("GITHUB_TOKEN is not usable", out.getvalue())


class Captures(LiveCheckTestCase):
    def test_a_capture_holds_each_response_with_an_allow_listed_header_set_and_a_label(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, output = self.run_check("--capture", tmp)
            files = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in Path(tmp).iterdir()}
        self.assertEqual(code, 0, output)
        manifest = files.pop("MANIFEST.json")
        self.assertTrue(manifest["data"].startswith("FIXTURE DATA, NOT FROM GITHUB"))
        self.assertIs(manifest["authenticated"], False)
        self.assertEqual(sorted(files), sorted(manifest["files"]))
        self.assertEqual(
            sorted(files),
            ["rate_limit.json", "repos__fixture-org__example-repo.json",
             "repos__fixture-org__example-repo__languages.json", "repos__fixture-org__missing-repo.json"],
        )  # fmt: skip
        for name, record in files.items():
            with self.subTest(file=name):
                self.assertEqual(set(record), {"request", "status", "headers", "body"})
                self.assertLessEqual(set(record["headers"]), set(check.CAPTURE_HEADERS))
        self.assertEqual(files["repos__fixture-org__missing-repo.json"]["status"], 404)
        example = files["repos__fixture-org__example-repo.json"]
        self.assertEqual(example["body"]["full_name"], "fixture-org/example-repo")


class TheVerdict(unittest.TestCase):
    """Only a clean pass against the real GitHub may say VERIFIED; anything else must not."""

    @staticmethod
    def report(*statuses: str) -> check.Report:
        report = check.Report(io.StringIO())
        report.results = [(status, f"check {i}") for i, status in enumerate(statuses)]
        return report

    def test_a_real_pass_is_verified_but_only_for_what_was_checked(self) -> None:
        code, line = check.verdict(self.report("PASS", "PASS", "SKIP"), real=True)
        self.assertEqual(code, 0)
        self.assertIn("VERIFIED for what was checked", line)
        self.assertIn("2 checks against api.github.com", line)
        self.assertIn("nothing else is", line)

    def test_a_stand_in_pass_never_says_verified(self) -> None:
        code, line = check.verdict(self.report("PASS", "PASS"), real=False)
        self.assertEqual(code, 0)
        self.assertIn("does NOT verify api.github.com", line)
        self.assertIn("UNVERIFIED", line)
        self.assertNotIn("VERIFIED for", line)

    def test_any_failure_fails_the_run_in_either_mode(self) -> None:
        for real in (True, False):
            with self.subTest(real=real):
                code, line = check.verdict(self.report("PASS", "FAIL", "PASS"), real=real)
                self.assertEqual(code, 1)
                self.assertIn("1 of 3 checks failed", line)
                self.assertNotIn("VERIFIED", line)

    def test_skips_are_not_passes(self) -> None:
        code, line = check.verdict(self.report("SKIP", "SKIP", "PASS"), real=True)
        self.assertEqual(code, 0)
        self.assertIn("1 check against api.github.com", line)


if __name__ == "__main__":
    unittest.main()
