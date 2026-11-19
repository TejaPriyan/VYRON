"""Release dates read out of a registry document: what counts as a date, what is left out and why.

Every document here is a fixture written by hand from the shape of PyPI's and npm's answers. Nothing was captured
from either registry, so these tests show how VYRON reads those shapes, not that the registries send them.
"""

import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from registry_fakes import pypi_files
from vyron_api.api.schemas import build_components
from vyron_api.registries import releases as releases_module
from vyron_api.registries.errors import RegistryError
from vyron_api.registries.releases import (
    DATE_MEANINGS,
    RELEASE_LEFT_OUT_KINDS,
    RELEASE_STATUSES,
    ReleaseDatesMissingError,
    npm_looks_like_pre_release,
    npm_release_list,
    parse_timestamp,
    pypi_looks_like_pre_release,
    pypi_release_list,
)

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)


def at(year: int, month: int = 1, day: int = 1, hour: int = 10) -> str:
    return f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:00:00.000000Z"


class ParsingATimestamp(unittest.TestCase):
    def test_a_zone_is_honoured_and_the_result_is_utc(self) -> None:
        self.assertEqual(parse_timestamp("2024-02-06T10:15:30.000000Z"), datetime(2024, 2, 6, 10, 15, 30, tzinfo=UTC))
        self.assertEqual(parse_timestamp("2024-02-06T10:15:30+02:00"), datetime(2024, 2, 6, 8, 15, 30, tzinfo=UTC))
        self.assertEqual(parse_timestamp("2024-02-06T00:30:00-05:00"), datetime(2024, 2, 6, 5, 30, tzinfo=UTC))

    def test_a_timestamp_without_a_zone_is_read_as_utc(self) -> None:
        moment = parse_timestamp("2024-02-06T10:15:30")
        self.assertEqual(moment, datetime(2024, 2, 6, 10, 15, 30, tzinfo=UTC))
        assert moment is not None
        self.assertEqual(moment.utcoffset(), timedelta(0))

    def test_anything_else_is_not_a_date(self) -> None:
        values: tuple[object, ...] = (
            None, 1700000000, 2024.5, [], {}, "", "yesterday", "2024-13-45T00:00:00Z", "x" * 41, "2024",
        )  # fmt: skip
        for value in values:
            with self.subTest(value=repr(value)[:30]):
                self.assertIsNone(parse_timestamp(value))


class ReadingAVersionNumberForAPreRelease(unittest.TestCase):
    def test_pypi_segments_that_mark_a_pre_release(self) -> None:
        for version in ("1.0.0rc1", "2.0.0a1", "2.0.0b1", "1.0.dev3", "1.0.0.dev0", "1.0.0-beta.1", "1.0c2", "3.0.0b"):
            with self.subTest(version=version):
                self.assertTrue(pypi_looks_like_pre_release(version))

    def test_pypi_versions_that_are_not(self) -> None:
        for version in ("1.0.0", "2024.1", "1.0.0.post1", "1!2.0", "1.0.0+dev1", "1.0.0+local.build1", "1.0.0abc"):
            with self.subTest(version=version):
                self.assertFalse(pypi_looks_like_pre_release(version))

    def test_npm_pre_releases_have_a_dash_suffix(self) -> None:
        for version in ("3.0.0-beta.1", "4.0.0-rc.1", "v1.0.0-0", "=1.2.3-alpha"):
            with self.subTest(version=version):
                self.assertTrue(npm_looks_like_pre_release(version))

    def test_npm_versions_that_are_not(self) -> None:
        for version in ("1.0.0", "1.0.0+exp-1", "10.20.30", "v2.0.0"):
            with self.subTest(version=version):
                self.assertFalse(npm_looks_like_pre_release(version))


class PyPIReleases(unittest.TestCase):
    def read(self, releases: object, **kwargs: int) -> releases_module.ReleaseList:
        found = pypi_release_list(releases, now=NOW, **kwargs)
        assert found is not None
        return found

    def test_the_date_of_a_version_is_the_earliest_upload_among_its_files(self) -> None:
        found = self.read({"1.0.0": pypi_files(at(2020, 5, 3), at(2020, 5, 1), at(2020, 5, 2))})
        self.assertEqual([r.released_at for r in found.records], [datetime(2020, 5, 1, 10, tzinfo=UTC)])
        self.assertEqual(found.date_meaning, "first_file_upload")

    def test_records_run_oldest_first_and_ties_break_on_the_version_text(self) -> None:
        found = self.read(
            {
                "2.0.0": pypi_files(at(2022)),
                "1.0.1": pypi_files(at(2021)),
                "1.0.0": pypi_files(at(2021)),
                "0.1.0": pypi_files(at(2019)),
            }
        )
        self.assertEqual([r.version for r in found.records], ["0.1.0", "1.0.0", "1.0.1", "2.0.0"])

    def test_a_version_is_yanked_only_when_every_file_is(self) -> None:
        every = pypi_files(at(2020), at(2020, 1, 2), yanked=True)
        some = pypi_files(at(2020, 2, 1), yanked=True) + pypi_files(at(2020, 2, 2))
        status = {
            r.version: r.status
            for r in self.read({"1.0.0": every, "1.1.0": some, "1.2.0": pypi_files(at(2021))}).records
        }
        self.assertEqual(status, {"1.0.0": "yanked", "1.1.0": "normal", "1.2.0": "normal"})

    def test_pre_releases_are_marked_from_the_number_and_stay_in_the_list(self) -> None:
        found = self.read({"1.0.0rc1": pypi_files(at(2020)), "1.0.0": pypi_files(at(2020, 2))})
        self.assertEqual([(r.version, r.pre_release) for r in found.records], [("1.0.0rc1", True), ("1.0.0", False)])

    def test_a_version_with_no_files_or_no_usable_time_is_counted_as_undated(self) -> None:
        bad_time = [{"upload_time_iso_8601": "last tuesday", "upload_time": None, "yanked": False}]
        found = self.read(
            {
                "1.0.0": pypi_files(at(2020)),
                "0.9.0": [],
                "0.9.1": None,
                "0.9.2": "not a list",
                "0.9.3": bad_time,
                "0.9.4": ["not a mapping"],
            }
        )
        self.assertEqual([r.version for r in found.records], ["1.0.0"])
        self.assertEqual(found.left_out, (("undated", 5),))

    def test_the_older_upload_time_is_the_fallback_and_a_missing_zone_means_utc(self) -> None:
        files = [{"upload_time": "2020-05-01T09:30:00", "yanked": False}]
        found = self.read({"1.0.0": files})
        self.assertEqual(found.records[0].released_at, datetime(2020, 5, 1, 9, 30, tzinfo=UTC))

    def test_dates_that_cannot_be_right_are_left_out_not_listed(self) -> None:
        found = self.read(
            {
                "1.0.0": pypi_files(at(2020)),
                "0.0.1": pypi_files(at(1980)),
                "9.9.9": pypi_files(at(2026, 11, 12)),  # more than a day after the moment of the read
            }
        )
        self.assertEqual([r.version for r in found.records], ["1.0.0"])
        self.assertEqual(found.left_out, (("implausible_date", 2),))

    def test_an_hour_ahead_is_clock_skew_not_an_error(self) -> None:
        stamp = (NOW + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual([r.version for r in self.read({"1.0.0": pypi_files(stamp)}).records], ["1.0.0"])

    def test_the_first_day_of_1991_is_the_earliest_accepted(self) -> None:
        found = self.read({"1.0": pypi_files("1991-01-01T00:00:00Z"), "0.1": pypi_files("1990-12-31T23:59:59Z")})
        self.assertEqual([r.version for r in found.records], ["1.0"])

    def test_version_strings_no_registry_would_issue_are_counted(self) -> None:
        found = self.read(
            {"1.0.0": pypi_files(at(2020)), "": pypi_files(at(2020)), " 1.0": pypi_files(at(2020)),
             "1.0\n": pypi_files(at(2020)), "9" * 101: pypi_files(at(2020))}
        )  # fmt: skip
        self.assertEqual([r.version for r in found.records], ["1.0.0"])
        self.assertEqual(found.left_out, (("unusable", 4),))

    def test_a_long_history_keeps_the_newest_and_says_how_many_were_dropped(self) -> None:
        table = {f"1.{index}.0": pypi_files(at(2010 + index)) for index in range(6)}
        found = self.read(table, cap=4)
        self.assertEqual([r.version for r in found.records], ["1.2.0", "1.3.0", "1.4.0", "1.5.0"])
        self.assertEqual(found.omitted_older, 2)

    def test_a_history_inside_the_cap_omits_nothing(self) -> None:
        found = self.read({"1.0.0": pypi_files(at(2020))}, cap=1)
        self.assertEqual((len(found.records), found.omitted_older), (1, 0))

    def test_left_out_kinds_come_in_the_documented_order_with_no_empty_ones(self) -> None:
        found = self.read({"1.0.0": pypi_files(at(2020)), "": [], "0.1": pypi_files(at(1980)), "0.2": []})
        self.assertEqual([kind for kind, _ in found.left_out], ["undated", "implausible_date", "unusable"])
        order = [kind for kind in RELEASE_LEFT_OUT_KINDS if kind in dict(found.left_out)]
        self.assertEqual([kind for kind, _ in found.left_out], order)

    def test_no_releases_object_is_an_answer_of_its_own_not_an_empty_list(self) -> None:
        absent: tuple[object, ...] = (None, [], "x", 3)
        for value in absent:
            with self.subTest(value=repr(value)):
                self.assertIsNone(pypi_release_list(value, now=NOW))
        empty = pypi_release_list({}, now=NOW)
        assert empty is not None
        self.assertEqual((empty.records, empty.left_out), ((), ()))

    def test_a_document_with_an_absurd_number_of_versions_is_refused_not_sampled(self) -> None:
        with mock.patch.object(releases_module, "MAX_VERSIONS_READ", 3):
            with self.assertRaises(RegistryError):
                pypi_release_list({str(i): [] for i in range(4)}, now=NOW)
            self.assertIsNotNone(pypi_release_list({str(i): [] for i in range(3)}, now=NOW))


class NpmReleases(unittest.TestCase):
    def read(self, times: object, versions: object, **kwargs: int) -> releases_module.ReleaseList:
        found = npm_release_list(times, versions, now=NOW, **kwargs)
        assert found is not None
        return found

    def test_the_date_is_the_registrys_time_entry_for_a_published_version(self) -> None:
        found = self.read(
            {"created": at(2012), "modified": at(2024), "1.0.0": at(2012, 6), "2.0.0": at(2014, 5)},
            {"1.0.0": {}, "2.0.0": {}},
        )
        self.assertEqual([(r.version, r.released_at.year) for r in found.records], [("1.0.0", 2012), ("2.0.0", 2014)])
        self.assertEqual(found.date_meaning, "publish_time")
        self.assertEqual(found.left_out, ())

    def test_created_and_modified_are_not_versions(self) -> None:
        found = self.read({"created": at(2012), "modified": at(2024), "unpublished": {"time": at(2020)}}, {})
        self.assertEqual((found.records, found.left_out), ((), ()))

    def test_a_time_entry_for_a_version_that_is_no_longer_published_is_counted_as_removed(self) -> None:
        found = self.read(
            {"created": at(2012), "1.0.0": at(2012, 6), "1.5.0": at(2013), "1.6.0": at(2013, 2)}, {"1.0.0": {}}
        )
        self.assertEqual([r.version for r in found.records], ["1.0.0"])
        self.assertEqual(found.left_out, (("removed", 2),))

    def test_a_published_version_with_no_time_entry_is_undated(self) -> None:
        found = self.read({"1.0.0": at(2012)}, {"1.0.0": {}, "2.0.0": {}})
        self.assertEqual([r.version for r in found.records], ["1.0.0"])
        self.assertEqual(found.left_out, (("undated", 1),))

    def test_a_time_entry_that_is_not_a_timestamp_makes_the_version_undated(self) -> None:
        found = self.read({"1.0.0": "soon", "2.0.0": 5, "3.0.0": at(2020)}, {"1.0.0": {}, "2.0.0": {}, "3.0.0": {}})
        self.assertEqual(([r.version for r in found.records], found.left_out), (["3.0.0"], (("undated", 2),)))

    def test_deprecated_is_npms_own_flag_on_the_version(self) -> None:
        versions = {
            "1.0.0": {"deprecated": "use 2.x"},
            "1.1.0": {"deprecated": ""},
            "1.2.0": {"deprecated": False},
            "1.3.0": {},
            "1.4.0": "not a mapping",
        }
        times = {version: at(2020, index + 1) for index, version in enumerate(versions)}
        found = self.read(times, versions)
        self.assertEqual(
            {r.version: r.status for r in found.records},
            {"1.0.0": "deprecated", "1.1.0": "normal", "1.2.0": "normal", "1.3.0": "normal", "1.4.0": "normal"},
        )

    def test_pre_releases_are_marked_from_the_number(self) -> None:
        found = self.read({"1.0.0-rc.1": at(2020), "1.0.0": at(2020, 2)}, {"1.0.0-rc.1": {}, "1.0.0": {}})
        self.assertEqual([(r.version, r.pre_release) for r in found.records], [("1.0.0-rc.1", True), ("1.0.0", False)])

    def test_dates_that_cannot_be_right_are_left_out_not_listed(self) -> None:
        found = self.read(
            {"1.0.0": at(2020), "0.1.0": at(1970), "9.0.0": at(2030)}, {"1.0.0": {}, "0.1.0": {}, "9.0.0": {}}
        )
        self.assertEqual(([r.version for r in found.records], found.left_out), (["1.0.0"], (("implausible_date", 2),)))

    def test_a_long_history_keeps_the_newest(self) -> None:
        versions: dict[str, object] = {f"1.{index}.0": {} for index in range(5)}
        times = {version: at(2015 + index) for index, version in enumerate(versions)}
        found = self.read(times, versions, cap=2)
        self.assertEqual(([r.version for r in found.records], found.omitted_older), (["1.3.0", "1.4.0"], 3))

    def test_a_document_without_a_time_object_is_an_answer_of_its_own(self) -> None:
        self.assertIsNone(npm_release_list(None, {"1.0.0": {}}, now=NOW))
        self.assertIsNone(npm_release_list([], {"1.0.0": {}}, now=NOW))
        self.assertIsNone(npm_release_list({"1.0.0": at(2020)}, None, now=NOW))

    def test_an_absurd_number_of_versions_is_refused(self) -> None:
        with mock.patch.object(releases_module, "MAX_VERSIONS_READ", 2):
            with self.assertRaises(RegistryError):
                npm_release_list({}, {str(i): {} for i in range(3)}, now=NOW)
            with self.assertRaises(RegistryError):
                npm_release_list({str(i): at(2020) for i in range(6)}, {}, now=NOW)


class TheWordsTheApiPromises(unittest.TestCase):
    """The response schema names the same values as the code that produces them."""

    def test_the_schema_and_the_reader_agree_on_every_word(self) -> None:
        components = build_components()
        self.assertEqual(tuple(components["ReleaseEntry"]["properties"]["status"]["enum"]), RELEASE_STATUSES)
        self.assertEqual(tuple(components["ReleaseLeftOut"]["properties"]["kind"]["enum"]), RELEASE_LEFT_OUT_KINDS)
        self.assertEqual(tuple(components["PackageReleases"]["properties"]["date_meaning"]["enum"]), DATE_MEANINGS)

    def test_the_cap_in_the_schema_is_the_cap_in_the_reader(self) -> None:
        schema = build_components()["PackageReleases"]["properties"]["releases"]
        self.assertEqual(schema["maxItems"], releases_module.MAX_RELEASES)

    def test_a_missing_dates_error_is_a_registry_error(self) -> None:
        self.assertTrue(issubclass(ReleaseDatesMissingError, RegistryError))


if __name__ == "__main__":
    unittest.main()
