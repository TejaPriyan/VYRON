import json
import unittest
from collections.abc import Mapping

from vyron_api.github.budget import RateLimitBudget
from vyron_api.github.retry import RawResponse, error_message, raise_for_status, send_with_retry
from vyron_api.providers.github import (
    GitHubAuthError,
    GitHubError,
    GitHubForbiddenError,
    GitHubMovedError,
    GitHubNotFoundError,
    GitHubRateLimitedError,
    GitHubUnavailableError,
    GitHubValidationError,
)

NOW = 1_000_000.0


def resp(status: int = 200, headers: Mapping[str, str] | None = None, body: object = None) -> RawResponse:
    payload = b"" if body is None else json.dumps(body).encode()
    return RawResponse(status, dict(headers or {}), payload)


class FakeSender:
    def __init__(self, *items: RawResponse | Exception) -> None:
        self.items = list(items)
        self.calls: list[Mapping[str, str]] = []

    async def __call__(self, headers: Mapping[str, str]) -> RawResponse:
        self.calls.append(dict(headers))
        item = self.items.pop(0) if len(self.items) > 1 else self.items[0]
        if isinstance(item, Exception):
            raise item
        return item


class Sleeps:
    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


class SendWithRetryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.sleeps = Sleeps()

    async def run_send(self, sender: FakeSender, **kwargs: object) -> RawResponse:
        return await send_with_retry(sender, sleep=self.sleeps, now=lambda: NOW, **kwargs)  # type: ignore[arg-type]

    async def test_success_needs_no_sleep_and_passes_headers(self) -> None:
        sender = FakeSender(resp(200, body={"ok": True}))
        result = await self.run_send(sender, extra_headers={"if-none-match": '"abc"'})
        self.assertEqual(result.json(), {"ok": True})
        self.assertEqual(sender.calls, [{"if-none-match": '"abc"'}])
        self.assertEqual(self.sleeps.waits, [])

    async def test_304_is_returned_as_is(self) -> None:
        self.assertEqual((await self.run_send(FakeSender(resp(304)))).status, 304)

    async def test_transient_errors_back_off_then_succeed(self) -> None:
        sender = FakeSender(resp(503), resp(502), resp(200))
        self.assertEqual((await self.run_send(sender)).status, 200)
        self.assertEqual(self.sleeps.waits, [1.0, 2.0])
        self.assertEqual(len(sender.calls), 3)

    async def test_jitter_is_added(self) -> None:
        await self.run_send(FakeSender(resp(503), resp(200)), jitter=lambda: 0.5)
        self.assertEqual(self.sleeps.waits, [1.5])

    async def test_exhausted_retries_raise_unavailable(self) -> None:
        sender = FakeSender(resp(503))
        with self.assertRaises(GitHubUnavailableError):
            await self.run_send(sender, max_retries=3)
        self.assertEqual(len(sender.calls), 4)  # one send + three retries
        self.assertEqual(self.sleeps.waits, [1.0, 2.0, 4.0])

    async def test_retry_after_is_honored(self) -> None:
        sender = FakeSender(resp(429, {"retry-after": "30"}), resp(200))
        await self.run_send(sender)
        self.assertEqual(self.sleeps.waits, [30.0])

    async def test_primary_limit_waits_for_reset(self) -> None:
        headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(NOW) + 10)}
        await self.run_send(FakeSender(resp(403, headers), resp(200)))
        self.assertEqual(self.sleeps.waits, [11.0])

    async def test_long_waits_are_not_slept_through(self) -> None:
        headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(NOW) + 1000)}
        with self.assertRaises(GitHubRateLimitedError) as caught:
            await self.run_send(FakeSender(resp(403, headers)))
        self.assertEqual(caught.exception.wait_seconds, 1001.0)
        self.assertEqual(self.sleeps.waits, [])

    async def test_persistent_rate_limit_after_retries(self) -> None:
        sender = FakeSender(resp(429, {"retry-after": "1"}))
        with self.assertRaises(GitHubRateLimitedError):
            await self.run_send(sender, max_retries=2)
        self.assertEqual(len(sender.calls), 3)

    async def test_plain_403_is_not_retried(self) -> None:
        sender = FakeSender(resp(403, {"x-ratelimit-remaining": "4000"}, {"message": "Forbidden"}))
        with self.assertRaises(GitHubForbiddenError):
            await self.run_send(sender)
        self.assertEqual((len(sender.calls), self.sleeps.waits), (1, []))

    async def test_secondary_limit_message_on_403_is_retried(self) -> None:
        sender = FakeSender(resp(403, {}, {"message": "You have exceeded a secondary rate limit"}), resp(200))
        await self.run_send(sender)
        self.assertEqual(self.sleeps.waits, [60.0])

    async def test_client_errors_map_to_typed_errors_without_retry(self) -> None:
        cases = {
            401: GitHubAuthError,
            404: GitHubNotFoundError,
            410: GitHubNotFoundError,
            422: GitHubValidationError,
            400: GitHubValidationError,
            451: GitHubForbiddenError,
            301: GitHubMovedError,
            302: GitHubMovedError,
            307: GitHubMovedError,
            308: GitHubMovedError,
        }
        for status, error_type in cases.items():
            with self.subTest(status=status):
                sender = FakeSender(resp(status, body={"message": "nope"}))
                with self.assertRaises(error_type):
                    await self.run_send(sender)
                self.assertEqual(len(sender.calls), 1)

    async def test_a_redirect_is_reported_without_following_or_repeating_its_location(self) -> None:
        location = "https://evil.example/steal?token=abc"
        sender = FakeSender(resp(301, {"location": location}, {"message": "Moved Permanently"}))
        with self.assertRaises(GitHubMovedError) as caught:
            await self.run_send(sender)
        self.assertEqual((len(sender.calls), self.sleeps.waits), (1, []))
        self.assertNotIn("evil.example", str(caught.exception))
        self.assertNotIn("token", str(caught.exception))

    async def test_network_errors_are_retried(self) -> None:
        sender = FakeSender(ConnectionResetError("reset"), TimeoutError(), resp(200))
        self.assertEqual((await self.run_send(sender)).status, 200)
        self.assertEqual(self.sleeps.waits, [1.0, 2.0])

    async def test_persistent_network_errors_raise_unavailable(self) -> None:
        with self.assertRaises(GitHubUnavailableError):
            await self.run_send(FakeSender(OSError("down")), max_retries=2)

    async def test_non_network_exceptions_propagate(self) -> None:
        with self.assertRaises(KeyError):
            await self.run_send(FakeSender(KeyError("bug")))


class ErrorHelpersTest(unittest.TestCase):
    def test_error_message_is_sanitized_and_truncated(self) -> None:
        message = "bad\x00thing\x1b[31m" + "x" * 500
        text = error_message(resp(422, body={"message": message}))
        self.assertNotIn("\x00", text)
        self.assertNotIn("\x1b", text)
        self.assertEqual(len(text), 200)

    def test_error_message_tolerates_non_json_and_wrong_shapes(self) -> None:
        for body in (b"<html>", b"", b"[]", b'{"message": 5}', b"\xff\xfe"):
            with self.subTest(body=body):
                self.assertEqual(error_message(RawResponse(500, {}, body)), "")

    def test_raise_for_status_passes_success(self) -> None:
        for status in (200, 201, 204, 304):
            raise_for_status(resp(status))

    def test_json_error_on_bad_body(self) -> None:
        with self.assertRaises(GitHubError):
            RawResponse(200, {}, b"not json").json()


class RateLimitBudgetTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = NOW
        self.budget = RateLimitBudget(reserve=10, now=lambda: self.now)

    def headers(self, remaining: int, resource: str = "core") -> dict[str, str]:
        return {
            "x-ratelimit-limit": "5000",
            "x-ratelimit-remaining": str(remaining),
            "x-ratelimit-used": str(5000 - remaining),
            "x-ratelimit-reset": str(int(NOW) + 600),
            "x-ratelimit-resource": resource,
        }

    def test_no_state_means_no_block(self) -> None:
        self.budget.check()

    def test_blocks_at_or_below_reserve_until_reset(self) -> None:
        self.budget.update(self.headers(11))
        self.budget.check()
        self.budget.update(self.headers(10))
        with self.assertRaises(GitHubRateLimitedError) as caught:
            self.budget.check()
        self.assertEqual(caught.exception.wait_seconds, 601.0)
        self.now = NOW + 601
        self.budget.check()  # window rolled over
        self.assertEqual(self.budget.snapshot(), {})

    def test_resources_are_tracked_separately(self) -> None:
        self.budget.update(self.headers(0, "search"))
        self.budget.check("core")
        with self.assertRaises(GitHubRateLimitedError):
            self.budget.check("search")
        self.assertEqual(self.budget.snapshot()["search"].remaining, 0)

    def test_responses_without_rate_limit_headers_are_ignored(self) -> None:
        self.budget.update({})
        self.budget.update({"x-ratelimit-remaining": "0"})  # no limit header: incomplete
        self.assertEqual(self.budget.snapshot(), {})

    def test_negative_reserve_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RateLimitBudget(reserve=-1)


if __name__ == "__main__":
    unittest.main()
