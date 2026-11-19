"""scripts/github_304_probe.py: its verdict logic and its behaviour against the local stand-in (fixture data).

These tests show the probe reads its own numbers correctly and refuses to guess. They say nothing about what the
real GitHub does with a 304; only running the probe on a machine that can reach api.github.com answers that.
"""

import _paths  # noqa: F401

import contextlib
import io
import socket
import unittest

import github_304_probe as probe
from standin_github import start_standin

SENTINEL_TOKEN = "ghp_SentinelTokenNotReal000000000001"


def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Judge(unittest.TestCase):
    def test_zero_cost_means_a_304_was_free(self) -> None:
        self.assertEqual(probe.judge([0, 0]), "a 304 did NOT count against the limit")

    def test_one_each_means_a_304_counted(self) -> None:
        self.assertEqual(probe.judge([1, 1]), "a 304 DID count against the limit (1 each)")

    def test_a_single_reading_is_judged_the_same_way(self) -> None:
        self.assertIn("did NOT", probe.judge([0]))
        self.assertIn("DID", probe.judge([1]))

    def test_disagreeing_readings_are_called_mixed_not_averaged(self) -> None:
        verdict = probe.judge([0, 1])
        self.assertTrue(verdict.startswith("mixed"), verdict)
        self.assertIn("[0, 1]", verdict)

    def test_a_cost_other_than_zero_or_one_is_mixed(self) -> None:
        self.assertTrue(probe.judge([2, 2]).startswith("mixed"))

    def test_a_missing_reading_means_it_could_not_tell(self) -> None:
        self.assertTrue(probe.judge([0, None]).startswith("could not tell"))

    def test_a_count_that_went_down_means_the_window_rolled_over(self) -> None:
        verdict = probe.judge([-40, 0])
        self.assertTrue(verdict.startswith("could not tell"), verdict)
        self.assertIn("rolled over", verdict)

    def test_with_no_readings_at_all_it_never_says_a_304_was_free(self) -> None:
        verdict = probe.judge([])
        self.assertTrue(verdict.startswith("could not tell"), verdict)
        self.assertNotIn("NOT count", verdict)


class Run(unittest.TestCase):
    def run_probe(self, *args: str, environ: dict[str, str] | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = probe.main(list(args), environ or {})
        return code, out.getvalue(), err.getvalue()

    def start(self) -> object:
        standin = start_standin()
        self.addCleanup(standin.server_close)
        self.addCleanup(standin.shutdown)
        return standin

    def test_against_the_stand_in_it_reports_what_the_stand_in_did_and_labels_it_as_fixture_data(self) -> None:
        standin = self.start()
        code, out, _ = self.run_probe("--base-url", standin.base, "--repo", "fixture-org/example-repo")
        self.assertEqual(code, 0, out)
        self.assertIn("STAND-IN, FIXTURE DATA", out)
        self.assertIn("says nothing about GitHub", out)
        self.assertIn("RESULT: a 304 DID count against the limit (1 each)", out)  # the stand-in counts every request
        self.assertIn("conditional GET #2: HTTP 304", out)

    def test_it_sends_exactly_one_plain_read_and_two_conditional_ones(self) -> None:
        standin = self.start()
        self.run_probe("--base-url", standin.base, "--repo", "fixture-org/example-repo")
        core = [e for e in standin.log_copy() if e["path"] != "/rate_limit"]
        self.assertEqual([e["path"] for e in core], ["/repos/fixture-org/example-repo"] * 3)

    def test_a_repository_that_does_not_answer_200_teaches_nothing_and_exits_2(self) -> None:
        standin = self.start()
        code, out, _ = self.run_probe("--base-url", standin.base, "--repo", "fixture-org/missing-repo")
        self.assertEqual(code, 2, out)
        self.assertIn("Nothing was learned", out)
        self.assertNotIn("RESULT:", out)

    def test_an_unreachable_server_exits_2_and_says_nothing_was_learned(self) -> None:
        code, out, _ = self.run_probe("--base-url", f"http://127.0.0.1:{closed_port()}")
        self.assertEqual(code, 2, out)
        self.assertIn("Nothing was learned", out)
        self.assertNotIn("RESULT:", out)

    def test_a_base_url_that_is_not_loopback_is_refused_before_any_request(self) -> None:
        code, out, err = self.run_probe("--base-url", "https://example.com")
        self.assertEqual(code, 2)
        self.assertIn("only for a local stand-in", err)
        self.assertNotIn("target:", out)

    def test_a_token_is_neither_printed_nor_sent_to_a_stand_in(self) -> None:
        standin = self.start()
        _, out, err = self.run_probe(
            "--base-url", standin.base, "--repo", "fixture-org/example-repo",
            environ={"GITHUB_TOKEN": SENTINEL_TOKEN},
        )  # fmt: skip
        self.assertNotIn(SENTINEL_TOKEN, out + err)
        self.assertIn("token:  not set", out)
        self.assertTrue(all("authorization" not in {k.lower() for k in e.get("headers", {})}
                            for e in standin.log_copy()))  # fmt: skip

    def test_the_real_target_with_a_token_would_say_it_is_set_without_showing_it(self) -> None:
        # Only the printing is checked; no request is made because the first call is patched out.
        original = probe.get
        calls: list[str | None] = []

        def refuse(base: str, path: str, token: str | None, headers: object = None) -> tuple[int, dict, bytes]:
            calls.append(token)
            raise OSError("no network in tests")

        probe.get = refuse  # type: ignore[assignment]
        self.addCleanup(setattr, probe, "get", original)
        code, out, err = self.run_probe(environ={"GITHUB_TOKEN": SENTINEL_TOKEN})
        self.assertEqual(code, 2)
        self.assertIn("token:  set (value not shown)", out)
        self.assertNotIn(SENTINEL_TOKEN, out + err)
        self.assertEqual(calls, [SENTINEL_TOKEN])  # it is handed to the one place that sends it, and nowhere else


if __name__ == "__main__":
    unittest.main()
