import unittest

from vyron_api.api.errors import (
    STATUS_FOR_CODE,
    ApiError,
    ErrorCode,
    FieldIssue,
    error_body,
    error_headers,
    to_api_error,
    validation_error,
)
from vyron_api.api.params import parse_path_params, parse_query_params
from vyron_api.api.routes import OPERATIONS
from vyron_api.domain.rules import InvalidRelationshipError
from vyron_api.github.normalize import MalformedGitHubPayloadError
from vyron_api.github.url import InvalidRepoReferenceError
from vyron_api.providers.ai import AIUnavailableError
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
from vyron_api.providers.graph import EntityNotFoundError, GraphUnavailableError

SEARCH = OPERATIONS["search"]
NEIGHBORS = OPERATIONS["get_entity_neighbors"]
UUID = "65f9394b-42af-5055-a3a8-72312e13e423"


def issues_for(route, query: str) -> dict[str, str]:
    with unittest.TestCase().assertRaises(ApiError) as caught:
        parse_query_params(route, query)
    return {i.field: i.issue for i in caught.exception.details}


class QueryParamsTest(unittest.TestCase):
    def test_defaults_and_types(self) -> None:
        self.assertEqual(
            parse_query_params(SEARCH, "q=react"),
            {"q": "react", "kind": None, "limit": 25, "cursor": None},
        )
        parsed = parse_query_params(SEARCH, "q=react&limit=100&kind=library&kind=framework")
        self.assertEqual(parsed["limit"], 100)
        self.assertEqual(parsed["kind"], ["library", "framework"])

    def test_percent_encoding_and_plus_are_decoded(self) -> None:
        self.assertEqual(parse_query_params(SEARCH, "q=c%2B%2B")["q"], "c++")
        self.assertEqual(parse_query_params(SEARCH, "q=next+js")["q"], "next js")

    def test_required_and_unknown(self) -> None:
        found = issues_for(SEARCH, "limit=5&bogus=1")
        self.assertEqual(found["q"], "is required")
        self.assertEqual(found["bogus"], "unknown query parameter")

    def test_strict_integers(self) -> None:
        for bad in ("abc", "1.0", " 1", "1 ", "+1", "٣", "1e2", ""):
            with self.subTest(bad=bad):
                self.assertIn("limit", issues_for(SEARCH, f"q=x&limit={bad}"))

    def test_bounds_are_enforced(self) -> None:
        self.assertIn("limit", issues_for(SEARCH, "q=x&limit=0"))
        self.assertIn("limit", issues_for(SEARCH, "q=x&limit=101"))
        self.assertIn("depth", issues_for(NEIGHBORS, "depth=4"))
        self.assertIn("max_nodes", issues_for(NEIGHBORS, "max_nodes=301"))

    def test_scalars_must_not_repeat(self) -> None:
        self.assertEqual(issues_for(SEARCH, "q=a&q=b")["q"], "must be given only once")

    def test_enum_arrays(self) -> None:
        self.assertIn("kind", issues_for(SEARCH, "q=x&kind=spaceship"))
        self.assertIn("kind", issues_for(SEARCH, "q=x&kind=library&kind=library"))  # uniqueItems
        self.assertIn("relation_type", issues_for(NEIGHBORS, "relation_type=likes"))
        self.assertEqual(
            parse_query_params(NEIGHBORS, "relation_type=USES&relation_type=BUILT_ON")["relation_type"],
            ["USES", "BUILT_ON"],
        )

    def test_booleans_are_strict(self) -> None:
        route = OPERATIONS["timeline"]
        self.assertTrue(parse_query_params(route, "include_predictions=true")["include_predictions"])
        self.assertFalse(parse_query_params(route, "")["include_predictions"])
        for bad in ("1", "yes", "True", ""):
            with self.subTest(bad=bad):
                self.assertIn("include_predictions", issues_for(route, f"include_predictions={bad}"))

    def test_control_characters_are_rejected(self) -> None:
        self.assertIn("q", issues_for(SEARCH, "q=a%00b"))
        self.assertIn("q", issues_for(SEARCH, "q=a%0Ab"))

    def test_search_text_length_limit(self) -> None:
        self.assertIn("q", issues_for(SEARCH, "q=" + "a" * 101))
        self.assertEqual(len(parse_query_params(SEARCH, "q=" + "a" * 100)["q"]), 100)

    def test_oversized_and_malformed_query_strings(self) -> None:
        self.assertIn("query", issues_for(SEARCH, "q=" + "a" * 3000))
        self.assertIn("query", issues_for(SEARCH, "&".join(f"k{i}=1" for i in range(40))))
        self.assertIn("query", issues_for(SEARCH, "q=%ff%fe"))  # invalid UTF-8

    def test_all_problems_are_reported_together(self) -> None:
        found = issues_for(SEARCH, "limit=0&kind=nope&extra=1")
        self.assertGreaterEqual(len(found), 4)


class PathParamsTest(unittest.TestCase):
    def test_uuid_validation(self) -> None:
        route = OPERATIONS["get_entity"]
        self.assertEqual(parse_path_params(route, {"entity_id": UUID}), {"entity_id": UUID})
        with self.assertRaises(ApiError) as caught:
            parse_path_params(route, {"entity_id": "../../etc/passwd"})
        self.assertEqual(caught.exception.status, 422)

    def test_slug_and_kind(self) -> None:
        route = OPERATIONS["get_entity_by_slug"]
        self.assertEqual(
            parse_path_params(route, {"kind": "language", "slug": "c-plus-plus"}),
            {"kind": "language", "slug": "c-plus-plus"},
        )
        for raw in ({"kind": "nope", "slug": "x"}, {"kind": "language", "slug": "Bad_Slug"}):
            with self.subTest(raw=raw), self.assertRaises(ApiError):
                parse_path_params(route, raw)


class ErrorMappingTest(unittest.TestCase):
    def test_every_code_has_a_status(self) -> None:
        self.assertEqual(set(STATUS_FOR_CODE), set(ErrorCode))

    def test_mapping(self) -> None:
        cases = [
            (EntityNotFoundError("x"), ErrorCode.NOT_FOUND),
            (GraphUnavailableError("neo4j down at bolt://secret"), ErrorCode.SERVICE_UNAVAILABLE),
            (InvalidRepoReferenceError("bad"), ErrorCode.VALIDATION_ERROR),
            (InvalidRelationshipError("bad"), ErrorCode.CONFLICT),
            (GitHubNotFoundError("x"), ErrorCode.NOT_FOUND),
            (GitHubValidationError("x"), ErrorCode.VALIDATION_ERROR),
            (GitHubRateLimitedError("x", 30), ErrorCode.UPSTREAM_RATE_LIMITED),
            (GitHubAuthError("token ghp_SECRET rejected"), ErrorCode.UPSTREAM_ERROR),
            (GitHubForbiddenError("x"), ErrorCode.UPSTREAM_ERROR),
            (GitHubUnavailableError("x"), ErrorCode.UPSTREAM_ERROR),
            (GitHubMovedError("x"), ErrorCode.NOT_FOUND),
            (GitHubError("x"), ErrorCode.UPSTREAM_ERROR),
            (MalformedGitHubPayloadError("x"), ErrorCode.UPSTREAM_ERROR),
            (AIUnavailableError("x"), ErrorCode.SERVICE_UNAVAILABLE),
            (RuntimeError("SELECT * FROM users"), ErrorCode.INTERNAL_ERROR),
            (KeyError("password"), ErrorCode.INTERNAL_ERROR),
        ]
        for exc, code in cases:
            with self.subTest(exc=type(exc).__name__):
                self.assertIs(to_api_error(exc).code, code)

    def test_internal_details_never_reach_the_message(self) -> None:
        secrets = ("ghp_SECRET", "bolt://secret", "SELECT", "password")
        for exc in (
            GitHubAuthError("token ghp_SECRET rejected"),
            GraphUnavailableError("neo4j down at bolt://secret"),
            RuntimeError("SELECT * FROM users"),
            KeyError("password"),
        ):
            message = to_api_error(exc).message
            for secret in secrets:
                self.assertNotIn(secret, message)

    def test_each_upstream_failure_says_which_it_is_and_none_repeat_upstream_text(self) -> None:
        upstream = "No linked GitHub account. Connect your GitHub account and retry."
        causes = {
            "token": GitHubAuthError(upstream),
            "denied": GitHubForbiddenError(upstream),
            "unreachable": GitHubUnavailableError(upstream),
            "unusable": MalformedGitHubPayloadError(upstream),
            "moved": GitHubMovedError(upstream),
        }
        messages = {name: to_api_error(exc).message for name, exc in causes.items()}
        self.assertEqual(len(set(messages.values())), len(messages), messages)  # the fix differs, so the words do
        for name, message in messages.items():
            with self.subTest(name):
                self.assertNotIn("linked", message)
                self.assertNotIn("account", message)

    def test_rate_limited_upstream_sets_retry_after(self) -> None:
        error = to_api_error(GitHubRateLimitedError("x", 61.2))
        self.assertEqual(error.retry_after_seconds, 62)
        self.assertEqual(error_headers(error), {"Retry-After": "62"})
        capped = to_api_error(GitHubRateLimitedError("x", 10**9))
        self.assertEqual(capped.retry_after_seconds, 3600)

    def test_api_error_passes_through(self) -> None:
        original = ApiError(ErrorCode.FORBIDDEN, "no")
        self.assertIs(to_api_error(original), original)

    def test_envelope_shape(self) -> None:
        body = error_body(validation_error([FieldIssue("limit", "bad")]), "req-1")
        self.assertEqual(
            body,
            {
                "error": {
                    "code": "validation_error",
                    "message": "request validation failed",
                    "request_id": "req-1",
                    "details": [{"field": "limit", "issue": "bad"}],
                }
            },
        )
        self.assertNotIn("details", error_body(ApiError(ErrorCode.NOT_FOUND, "x"), "r")["error"])

    def test_details_are_capped(self) -> None:
        many = [FieldIssue(f"f{i}", "bad") for i in range(100)]
        self.assertEqual(len(error_body(validation_error(many), "r")["error"]["details"]), 20)


if __name__ == "__main__":
    unittest.main()
