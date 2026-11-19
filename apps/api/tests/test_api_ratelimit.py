import threading
import unittest

from vyron_api.api.ratelimit import DEFAULT_LIMITS, FixedWindowRateLimiter, Limit
from vyron_api.api.routes import RateBucket


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def limiter(clock: Clock, **overrides: Limit) -> FixedWindowRateLimiter:
    limits = dict(DEFAULT_LIMITS)
    for name, limit in overrides.items():
        limits[RateBucket(name)] = limit
    return FixedWindowRateLimiter(limits, now=clock)


class RateLimiterTest(unittest.TestCase):
    def test_allows_up_to_the_limit_then_blocks(self) -> None:
        clock = Clock()
        rl = limiter(clock, auth=Limit(3, 60))
        decisions = [rl.check("1.1.1.1", RateBucket.AUTH) for _ in range(5)]
        self.assertEqual([d.allowed for d in decisions], [True, True, True, False, False])
        self.assertEqual([d.remaining for d in decisions], [2, 1, 0, 0, 0])
        self.assertEqual(decisions[0].limit, 3)

    def test_window_resets(self) -> None:
        clock = Clock()
        rl = limiter(clock, auth=Limit(1, 60))
        self.assertTrue(rl.check("a", RateBucket.AUTH).allowed)
        blocked = rl.check("a", RateBucket.AUTH)
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.reset_seconds, 60)
        clock.now += 59
        self.assertFalse(rl.check("a", RateBucket.AUTH).allowed)
        clock.now += 1
        self.assertTrue(rl.check("a", RateBucket.AUTH).allowed)

    def test_clients_and_buckets_are_independent(self) -> None:
        clock = Clock()
        rl = limiter(clock, auth=Limit(1, 60))
        self.assertTrue(rl.check("a", RateBucket.AUTH).allowed)
        self.assertTrue(rl.check("b", RateBucket.AUTH).allowed)
        self.assertTrue(rl.check("a", RateBucket.READ).allowed)
        self.assertFalse(rl.check("a", RateBucket.AUTH).allowed)

    def test_reset_seconds_is_at_least_one(self) -> None:
        clock = Clock()
        rl = limiter(clock, auth=Limit(1, 60))
        rl.check("a", RateBucket.AUTH)
        clock.now += 59.9999
        self.assertGreaterEqual(rl.check("a", RateBucket.AUTH).reset_seconds, 1)

    def test_memory_is_bounded(self) -> None:
        clock = Clock()
        rl = FixedWindowRateLimiter(DEFAULT_LIMITS, max_keys=100, now=clock)
        for i in range(1000):
            rl.check(f"client-{i}", RateBucket.READ)
        self.assertLessEqual(len(rl._windows), 100)

    def test_thread_safety_never_exceeds_the_limit(self) -> None:
        rl = limiter(Clock(), search=Limit(50, 60))
        allowed: list[bool] = []

        def hammer() -> None:
            for _ in range(20):
                allowed.append(rl.check("shared", RateBucket.SEARCH).allowed)

        threads = [threading.Thread(target=hammer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(allowed), 50)
        self.assertEqual(len(allowed), 160)

    def test_configuration_is_validated(self) -> None:
        with self.assertRaises(ValueError):
            Limit(0, 60)
        with self.assertRaises(ValueError):
            Limit(1, 0)
        with self.assertRaises(ValueError):
            FixedWindowRateLimiter({RateBucket.READ: Limit(1, 1)})  # buckets missing
        with self.assertRaises(ValueError):
            FixedWindowRateLimiter(DEFAULT_LIMITS, max_keys=0)

    def test_auth_is_the_strictest_bucket(self) -> None:
        rates = {b: lim.max_requests / lim.window_seconds for b, lim in DEFAULT_LIMITS.items()}
        self.assertEqual(min(rates, key=rates.get), RateBucket.AUTH)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
