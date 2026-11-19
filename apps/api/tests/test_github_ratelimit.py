import unittest

from vyron_api.github.ratelimit import (
    MIN_SECONDARY_WAIT_SECONDS,
    RateLimitState,
    decide_retry,
    parse_rate_limit_headers,
)

NOW = 1_000_000.0


class ParseHeadersTest(unittest.TestCase):
    def test_parses_documented_headers_case_insensitively(self) -> None:
        state = parse_rate_limit_headers(
            {
                "X-RateLimit-Limit": "5000",
                "x-ratelimit-remaining": "4999",
                "X-RATELIMIT-USED": "1",
                "x-ratelimit-reset": "1700000000",
                "x-ratelimit-resource": "core",
            }
        )
        self.assertEqual(
            state,
            RateLimitState(
                limit=5000,
                remaining=4999,
                used=1,
                reset_epoch=1_700_000_000,
                resource="core",
                retry_after_seconds=None,
            ),
        )
        self.assertFalse(state.exhausted)

    def test_invalid_values_become_none(self) -> None:
        state = parse_rate_limit_headers(
            {
                "x-ratelimit-remaining": "lots",
                "x-ratelimit-reset": "-5",
                "retry-after": "Wed, 21 Oct",
            }
        )
        self.assertIsNone(state.remaining)
        self.assertIsNone(state.reset_epoch)
        self.assertIsNone(state.retry_after_seconds)

    def test_zero_remaining_is_exhausted(self) -> None:
        self.assertTrue(parse_rate_limit_headers({"x-ratelimit-remaining": "0"}).exhausted)

    def test_empty_headers(self) -> None:
        self.assertEqual(parse_rate_limit_headers({}), RateLimitState())


class DecideRetryTest(unittest.TestCase):
    def test_retry_after_is_honored(self) -> None:
        decision = decide_retry(429, {"retry-after": "30"}, attempt=0, now=NOW)
        self.assertTrue(decision.retry)
        self.assertEqual(decision.wait_seconds, 30.0)
        self.assertEqual(decision.reason, "retry-after")

    def test_primary_limit_waits_for_reset(self) -> None:
        headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(NOW) + 100)}
        decision = decide_retry(403, headers, attempt=0, now=NOW)
        self.assertTrue(decision.retry)
        self.assertEqual(decision.wait_seconds, 101.0)
        self.assertEqual(decision.reason, "primary-limit-reset")

    def test_reset_in_the_past_waits_only_the_margin(self) -> None:
        headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(NOW) - 500)}
        self.assertEqual(decide_retry(403, headers, attempt=0, now=NOW).wait_seconds, 1.0)

    def test_secondary_limit_backs_off_from_one_minute(self) -> None:
        waits = [decide_retry(429, {}, attempt=n, now=NOW).wait_seconds for n in range(3)]
        self.assertEqual(waits, [MIN_SECONDARY_WAIT_SECONDS * f for f in (1, 2, 4)])

    def test_403_with_rate_limit_message_is_retried(self) -> None:
        decision = decide_retry(
            403, {}, attempt=0, now=NOW, message="You have exceeded a secondary rate limit."
        )
        self.assertTrue(decision.retry)
        self.assertEqual(decision.reason, "secondary-limit-backoff")

    def test_plain_403_is_not_retried(self) -> None:
        decision = decide_retry(
            403,
            {"x-ratelimit-remaining": "4000"},
            attempt=0,
            now=NOW,
            message="Resource not accessible",
        )
        self.assertFalse(decision.retry)
        self.assertEqual(decision.reason, "forbidden")

    def test_transient_errors_back_off_exponentially(self) -> None:
        waits = [decide_retry(503, {}, attempt=n, now=NOW).wait_seconds for n in range(4)]
        self.assertEqual(waits, [1.0, 2.0, 4.0, 8.0])

    def test_jitter_is_added(self) -> None:
        self.assertEqual(decide_retry(502, {}, attempt=1, now=NOW, jitter=0.25).wait_seconds, 2.25)

    def test_client_errors_are_not_retried(self) -> None:
        for status in (400, 401, 404, 410, 422):
            with self.subTest(status=status):
                decision = decide_retry(status, {}, attempt=0, now=NOW)
                self.assertFalse(decision.retry)
                self.assertEqual(decision.reason, "not-retryable")

    def test_attempt_cap_stops_retries(self) -> None:
        decision = decide_retry(503, {}, attempt=5, now=NOW, max_attempts=5)
        self.assertFalse(decision.retry)
        self.assertEqual(decision.reason, "max-attempts")

    def test_invalid_arguments_raise(self) -> None:
        with self.assertRaises(ValueError):
            decide_retry(503, {}, attempt=-1, now=NOW)
        with self.assertRaises(ValueError):
            decide_retry(503, {}, attempt=0, now=NOW, jitter=-1.0)


if __name__ == "__main__":
    unittest.main()
