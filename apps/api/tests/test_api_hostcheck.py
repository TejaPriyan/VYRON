import unittest

from vyron_api.api.hostcheck import LOOPBACK_HOSTS, host_allowed, hostname_of, is_loopback_bind


class HostnameOfTest(unittest.TestCase):
    def test_plain_hosts_with_and_without_a_port(self) -> None:
        cases = {
            "localhost": "localhost",
            "localhost:8000": "localhost",
            "LocalHost:80": "localhost",
            "127.0.0.1:5173": "127.0.0.1",
            "example.com": "example.com",
            "  example.com  ": "example.com",
            "[::1]": "::1",
            "[::1]:8000": "::1",
            "[2001:db8::1]:443": "2001:db8::1",
        }
        for header, expected in cases.items():
            with self.subTest(header=header):
                self.assertEqual(hostname_of(header), expected)

    def test_anything_that_is_not_a_host_and_optional_port_is_malformed(self) -> None:
        for header in (
            None, "", " ", ":8000", "localhost:", "localhost:abc", "localhost:80:80", "::1", "[::1", "[::1]x",
            "[::1]:", "[]", "[]:80", "a" * 300,
        ):  # fmt: skip
            with self.subTest(header=header):
                self.assertIsNone(hostname_of(header))

    def test_user_info_and_paths_are_kept_in_the_name_so_no_list_matches_them(self) -> None:
        # They are not stripped down to "localhost": that would turn a hostile header into an allowed one.
        self.assertEqual(hostname_of("user@localhost"), "user@localhost")
        self.assertEqual(hostname_of("localhost/path"), "localhost/path")
        self.assertFalse(host_allowed("user@localhost", LOOPBACK_HOSTS))
        self.assertFalse(host_allowed("localhost/path", LOOPBACK_HOSTS))

    def test_an_ipv6_address_without_brackets_is_refused_not_guessed(self) -> None:
        self.assertIsNone(hostname_of("::1"))
        self.assertIsNone(hostname_of("::1:8000"))


class HostAllowedTest(unittest.TestCase):
    def test_membership_ignores_case_and_port(self) -> None:
        self.assertTrue(host_allowed("LOCALHOST:8000", LOOPBACK_HOSTS))
        self.assertTrue(host_allowed("[::1]:8000", LOOPBACK_HOSTS))
        self.assertTrue(host_allowed("vyron.example", {"VYRON.example"}))

    def test_lookalikes_are_refused(self) -> None:
        for header in (
            "localhost.evil.example", "evil.example", "127.0.0.1.evil.example", "localhost.", "0.0.0.0",
            "127.0.0.2", "localhost@evil.example", None, "",
        ):  # fmt: skip
            with self.subTest(header=header):
                self.assertFalse(host_allowed(header, LOOPBACK_HOSTS))

    def test_an_empty_list_refuses_everything(self) -> None:
        self.assertFalse(host_allowed("localhost", set()))


class LoopbackBindTest(unittest.TestCase):
    def test_which_addresses_only_accept_local_connections(self) -> None:
        for host in ("127.0.0.1", "localhost", "LOCALHOST", "::1"):
            with self.subTest(host=host):
                self.assertTrue(is_loopback_bind(host))
        for host in ("0.0.0.0", "::", "192.168.1.5", "example.com", "127.0.0.2", ""):
            with self.subTest(host=host):
                self.assertFalse(is_loopback_bind(host))


if __name__ == "__main__":
    unittest.main()
