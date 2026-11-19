import unittest

from vyron_api.github.pagination import (
    UnsafePaginationUrlError,
    next_page_url,
    parse_link_header,
    validate_pagination_url,
)

API = "https://api.github.com/repositories/1/commits"
TYPICAL = (
    f'<{API}?page=1>; rel="prev", <{API}?page=3>; rel="next", '
    f'<{API}?page=34>; rel="last", <{API}?page=1>; rel="first"'
)


class ParseLinkHeaderTest(unittest.TestCase):
    def test_typical_header(self) -> None:
        links = parse_link_header(TYPICAL)
        self.assertEqual(set(links), {"prev", "next", "last", "first"})
        self.assertEqual(links["next"], f"{API}?page=3")

    def test_only_a_subset_of_rels_is_fine(self) -> None:
        self.assertEqual(parse_link_header(f'<{API}?page=2>; rel="next"'), {"next": f"{API}?page=2"})

    def test_unquoted_rel_multiple_rels_and_case(self) -> None:
        links = parse_link_header(f"<{API}?a=1>; rel=next, <{API}?b=2>; REL=\"first last\"")
        self.assertEqual(links["next"], f"{API}?a=1")
        self.assertEqual(links["first"], links["last"])

    def test_first_url_per_rel_wins(self) -> None:
        links = parse_link_header(f'<{API}?a=1>; rel="next", <{API}?a=2>; rel="next"')
        self.assertEqual(links["next"], f"{API}?a=1")

    def test_extra_params_are_tolerated(self) -> None:
        links = parse_link_header(f'<{API}?a=1>; title="x, y"; rel="next"')
        self.assertEqual(links.get("next"), f"{API}?a=1")

    def test_garbage_and_oversize_yield_nothing(self) -> None:
        for value in (None, "", "garbage", "<>", "<x>", 'rel="next"', "<x>; foo=bar", "a" * 9000):
            with self.subTest(value=(value or "")[:20]):
                self.assertEqual(parse_link_header(value).get("next"), None)


class NextPageUrlTest(unittest.TestCase):
    def test_returns_next_url(self) -> None:
        self.assertEqual(next_page_url({"Link": TYPICAL}), f"{API}?page=3")

    def test_header_name_is_case_insensitive(self) -> None:
        self.assertEqual(next_page_url({"LINK": TYPICAL}), f"{API}?page=3")

    def test_last_page_has_no_next(self) -> None:
        self.assertIsNone(next_page_url({"link": f'<{API}?page=1>; rel="prev"'}))
        self.assertIsNone(next_page_url({}))

    def test_unsafe_urls_are_refused(self) -> None:
        for url in (
            "https://evil.example/x",
            "https://api.github.com.evil.example/x",
            "http://api.github.com/x",
            "https://api.github.com:8443/x",
            "https://user:pw@api.github.com/x",
            "https://user@api.github.com/x",
            "//api.github.com/x",
            "/relative",
            "https://[::1/x",
            "ftp://api.github.com/x",
        ):
            with self.subTest(url=url), self.assertRaises(UnsafePaginationUrlError):
                next_page_url({"link": f'<{url}>; rel="next"'})

    def test_custom_allowed_host(self) -> None:
        self.assertEqual(
            validate_pagination_url("https://ghe.example/x", allowed_host="ghe.example"),
            "https://ghe.example/x",
        )


if __name__ == "__main__":
    unittest.main()
