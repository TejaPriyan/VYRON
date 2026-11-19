import unittest
from datetime import UTC, date, datetime

from vyron_api.domain.enums import DatePrecision, Reliability, SourceType
from vyron_api.domain.evidence import (
    Claim,
    Evidence,
    InvalidSourceUrlError,
    Source,
    unsupported_claims,
    validate_source_url,
)
from vyron_api.domain.provenance import Provenance
from vyron_api.domain.timeline import FutureEventNotLabelledError, TimelineEvent, is_future

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


class ValidateSourceUrlTest(unittest.TestCase):
    def test_accepts_public_urls(self) -> None:
        for url in (
            "https://docs.github.com/en/rest/rate-limit",
            "http://example.org/a?b=c#d",
            "https://8.8.8.8/x",
            "https://sub.domain.example.co.uk:8443/path",
        ):
            with self.subTest(url=url):
                self.assertEqual(validate_source_url(url), url)

    def test_rejects_unsafe_or_malformed_urls(self) -> None:
        rejected = [
            "",
            "ftp://example.com/file",
            "javascript:alert(1)",
            "file:///etc/passwd",
            "//example.com/x",
            "example.com/x",
            "https://user:pw@example.com/",
            "https://user@example.com/",
            "https://localhost/x",
            "https://app.localhost/x",
            "https://printer.local/x",
            "https://db.internal/x",
            "https://intranet/x",
            "https://127.0.0.1/x",
            "https://10.0.0.5/x",
            "https://192.168.1.1/x",
            "https://172.16.0.1/x",
            "https://169.254.169.254/latest/meta-data",
            "https://[::1]/x",
            "https://0.0.0.0/x",
            "https://example.com:notaport/x",
            "https://exa mple.com/x",
            "https://example.com/é",
            "https://example.com/" + "a" * 2100,
        ]
        for url in rejected:
            with self.subTest(url=url[:60]), self.assertRaises(InvalidSourceUrlError):
                validate_source_url(url)


class SourceEvidenceClaimTest(unittest.TestCase):
    def make_source(self, **overrides: object) -> Source:
        fields: dict[str, object] = {
            "id": "s1",
            "url": "https://docs.github.com/en/rest",
            "title": "GitHub REST API",
            "source_type": SourceType.OFFICIAL_DOCS,
            "retrieved_at": NOW,
            "reliability": Reliability.PRIMARY,
        }
        fields.update(overrides)
        return Source(**fields)  # type: ignore[arg-type]

    def test_valid_source(self) -> None:
        self.assertEqual(self.make_source().reliability, Reliability.PRIMARY)

    def test_source_rejects_bad_input(self) -> None:
        for overrides in (
            {"url": "https://localhost/x"},
            {"title": "  "},
            {"id": ""},
            {"retrieved_at": datetime(2026, 10, 5, 12, 0)},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.make_source(**overrides)

    def test_source_defaults_to_unverified_reliability(self) -> None:
        source = Source("s2", "https://example.org/a", "A", SourceType.BLOG, NOW)
        self.assertEqual(source.reliability, Reliability.UNVERIFIED)

    def test_evidence_validation(self) -> None:
        evidence = Evidence("e1", "Python was first released in 1991", "s1", "ent-1", NOW)
        self.assertIsNone(evidence.locator)
        for kwargs in (
            {"claim": " "},
            {"claim": "x" * 1001},
            {"source_id": ""},
            {"entity_id": ""},
            {"retrieved_at": datetime(2026, 1, 1)},
        ):
            fields: dict[str, object] = {
                "id": "e1",
                "claim": "ok",
                "source_id": "s1",
                "entity_id": "ent-1",
                "retrieved_at": NOW,
            }
            fields.update(kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Evidence(**fields)  # type: ignore[arg-type]

    def test_claim_rules(self) -> None:
        with self.assertRaises(ValueError):
            Claim("x", Provenance.SOURCED)
        with self.assertRaises(ValueError):
            Claim(" ", Provenance.AI_GENERATED)
        with self.assertRaises(ValueError):
            Claim("x", Provenance.ESTIMATED, confidence=1.5)
        self.assertEqual(Claim("x", Provenance.SOURCED, ("e1",), 0.5).confidence, 0.5)

    def test_unsupported_claims_are_everything_not_sourced(self) -> None:
        claims = [
            Claim("a", Provenance.SOURCED, ("e1",)),
            Claim("b", Provenance.AI_GENERATED),
            Claim("c", Provenance.PREDICTED),
        ]
        self.assertEqual([c.text for c in unsupported_claims(claims)], ["b", "c"])


class TimelineTest(unittest.TestCase):
    TODAY = date(2026, 10, 5)

    def test_is_future_by_precision(self) -> None:
        cases = [
            (date(2026, 10, 6), DatePrecision.DAY, True),
            (date(2026, 10, 5), DatePrecision.DAY, False),
            (date(2026, 11, 1), DatePrecision.MONTH, True),
            (date(2026, 10, 31), DatePrecision.MONTH, False),
            (date(2027, 1, 1), DatePrecision.YEAR, True),
            (date(2026, 12, 31), DatePrecision.YEAR, False),
            (date(2030, 1, 1), DatePrecision.DECADE, True),
            (date(2020, 1, 1), DatePrecision.DECADE, False),
            (date(2026, 1, 1), DatePrecision.DECADE, False),
        ]
        for occurred_on, precision, expected in cases:
            with self.subTest(occurred_on=occurred_on, precision=precision):
                self.assertEqual(is_future(occurred_on, precision, self.TODAY), expected)

    def make(self, **overrides: object) -> TimelineEvent:
        fields: dict[str, object] = {
            "id": "t1",
            "entity_id": "e1",
            "occurred_on": date(1991, 1, 1),
            "precision": DatePrecision.YEAR,
            "title": "Python first released",
            "event_type": "release",
            "provenance": Provenance.SEED,
        }
        fields.update(overrides)
        return TimelineEvent(**fields)  # type: ignore[arg-type]

    def test_past_event_passes(self) -> None:
        self.make().check_labelled_for(self.TODAY)

    def test_future_event_must_be_a_prediction(self) -> None:
        event = self.make(occurred_on=date(2030, 1, 1))
        with self.assertRaises(FutureEventNotLabelledError):
            event.check_labelled_for(self.TODAY)
        self.make(
            occurred_on=date(2030, 1, 1), provenance=Provenance.PREDICTED, is_prediction=True
        ).check_labelled_for(self.TODAY)

    def test_prediction_flag_must_match_provenance(self) -> None:
        with self.assertRaises(ValueError):
            self.make(is_prediction=True)
        with self.assertRaises(ValueError):
            self.make(provenance=Provenance.PREDICTED)


if __name__ == "__main__":
    unittest.main()
