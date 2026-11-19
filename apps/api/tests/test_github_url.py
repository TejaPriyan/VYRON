import unittest

from vyron_api.github.url import (
    GitHubTarget,
    InvalidRepoReferenceError,
    OwnerRef,
    RepoRef,
    TargetKind,
    parse_github_target,
    parse_owner_reference,
    parse_repo_reference,
    repo_ref_from_parts,
)

FLASK = RepoRef("pallets", "flask")


class ParseRepoReferenceTest(unittest.TestCase):
    def test_accepted_forms(self) -> None:
        accepted = [
            "pallets/flask",
            "  pallets/flask  ",
            "https://github.com/pallets/flask",
            "http://github.com/pallets/flask",
            "github.com/pallets/flask",
            "www.github.com/pallets/flask",
            "https://github.com/pallets/flask.git",
            "https://github.com/pallets/flask/",
            "https://github.com/pallets/flask/tree/main/src",
            "https://github.com/pallets/flask?tab=readme#top",
            "git@github.com:pallets/flask.git",
            "git@github.com:pallets/flask",
            "ssh://git@github.com/pallets/flask.git",
        ]
        for raw in accepted:
            with self.subTest(raw=raw):
                self.assertEqual(parse_repo_reference(raw), FLASK)

    def test_case_is_preserved_but_key_is_case_insensitive(self) -> None:
        ref = parse_repo_reference("HTTPS://GitHub.com/Pallets/Flask")
        self.assertEqual(ref.full_name, "Pallets/Flask")
        self.assertEqual(ref.key, "pallets/flask")

    def test_derived_urls_are_canonical(self) -> None:
        ref = parse_repo_reference("http://www.github.com/pallets/flask.git")
        self.assertEqual(ref.html_url, "https://github.com/pallets/flask")
        self.assertEqual(ref.api_path, "/repos/pallets/flask")

    def test_single_character_owner_and_dotted_repo(self) -> None:
        self.assertEqual(parse_repo_reference("a/socket.io"), RepoRef("a", "socket.io"))

    def test_rejected_inputs(self) -> None:
        rejected = [
            "",
            "   ",
            "flask",
            "a/b/c",
            "/a/b",
            "https://evil.com/pallets/flask",
            "https://github.com.evil.com/pallets/flask",
            "https://evil.com/github.com/pallets/flask",
            "https://github.com./pallets/flask",
            "https://user@github.com/a/b",
            "https://user:pw@github.com/a/b",
            "https://github.com:8443/a/b",
            "ftp://github.com/a/b",
            "javascript://github.com/a/b",
            "https://github.com/a",
            "https://github.com//b",
            "https://[::1/a/b",
            "ssh://evil@github.com/a/b",
            "ssh://git:pw@github.com/a/b",
            "ssh://git@evil.com/a/b",
            "ssh://github.com/a/b",
            "git@evil.com:a/b",
            "git@github.com:a/b/c",
            "git@github.com:",
            "a/..",
            "a/.",
            "a/.git",
            "../etc/passwd",
            "..%2Fb/c",
            "a/b%2f",
            "a b/c",
            "a/b\nc",
            "a\\b/c",
            "é/b",
            "-a/b",
            "a-/b",
            "a--b/c",
            "a" * 40 + "/b",
            "a/" + "b" * 101,
            "https://github.com/orgs/foo",
            "https://github.com/" + "a" * 400 + "/b",
        ]
        for raw in rejected:
            with self.subTest(raw=raw[:60]), self.assertRaises(InvalidRepoReferenceError):
                parse_repo_reference(raw)

    def test_boundary_lengths_are_accepted(self) -> None:
        owner, repo = "a" * 39, "b" * 100
        self.assertEqual(parse_repo_reference(f"{owner}/{repo}"), RepoRef(owner, repo))

    def test_error_is_a_value_error(self) -> None:
        with self.assertRaises(ValueError):
            parse_repo_reference("nope")


class RepoRefFromPartsTest(unittest.TestCase):
    """Route path segments are validated as exactly an owner and a repository, never re-read as a link."""

    def test_valid_parts(self) -> None:
        self.assertEqual(repo_ref_from_parts("vercel", "next.js"), RepoRef("vercel", "next.js"))
        self.assertEqual(repo_ref_from_parts("a", "b_c-d.e"), RepoRef("a", "b_c-d.e"))
        self.assertEqual(repo_ref_from_parts("pallets", "flask.git"), FLASK)  # the suffix is dropped, as in links

    def test_values_that_would_parse_differently_as_a_link_are_rejected(self) -> None:
        rejected = [
            ("github.com", "vercel"),  # as one string this reads as a host and an owner
            ("https:", "github.com"),
            ("vercel/next.js", "x"),
            ("-bad", "repo"),
            ("bad-", "repo"),
            ("a--b", "repo"),
            ("under_score", "repo"),
            ("orgs", "repo"),  # a site route, not an account
            ("vercel", ""),
            ("vercel", "."),
            ("vercel", ".."),
            ("vercel", "a b"),
            ("vercel", "x" * 101),
            ("x" * 40, "repo"),
            ("vercel", "next.js\n"),
            ("vercel", "%2e%2e"),
        ]
        for owner, repo in rejected:
            with self.subTest(owner=owner, repo=repo):
                with self.assertRaises(InvalidRepoReferenceError):
                    repo_ref_from_parts(owner, repo)


class ParseOwnerReferenceTest(unittest.TestCase):
    def test_accepted(self) -> None:
        for raw in (
            "pallets",
            "github.com/pallets",
            "https://github.com/pallets",
            "https://github.com/pallets/",
            "https://github.com/pallets/flask",
            "https://github.com/pallets?tab=repositories",
        ):
            with self.subTest(raw=raw):
                self.assertEqual(parse_owner_reference(raw), OwnerRef("pallets"))

    def test_rejected(self) -> None:
        for raw in ("", "a/b/c", "orgs", "https://evil.com/pallets", "-bad", "a_b", "github.com"):
            with self.subTest(raw=raw), self.assertRaises(InvalidRepoReferenceError):
                parse_owner_reference(raw)

    def test_derived(self) -> None:
        owner = parse_owner_reference("Pallets")
        self.assertEqual((owner.key, owner.html_url, owner.api_path), (
            "pallets", "https://github.com/Pallets", "/users/Pallets"))  # fmt: skip


class ParseGitHubTargetTest(unittest.TestCase):
    def test_classification(self) -> None:
        base = "https://github.com/pallets/flask"
        cases: list[tuple[str, GitHubTarget]] = [
            ("pallets", GitHubTarget(TargetKind.OWNER, "pallets")),
            ("https://github.com/pallets", GitHubTarget(TargetKind.OWNER, "pallets")),
            ("pallets/flask", GitHubTarget(TargetKind.REPOSITORY, "pallets", "flask")),
            (base, GitHubTarget(TargetKind.REPOSITORY, "pallets", "flask")),
            (f"{base}/issues", GitHubTarget(TargetKind.REPOSITORY, "pallets", "flask")),
            (f"{base}/actions/runs/1", GitHubTarget(TargetKind.REPOSITORY, "pallets", "flask")),
            (f"{base}/releases/latest", GitHubTarget(TargetKind.REPOSITORY, "pallets", "flask")),
            (f"{base}/issues/42", GitHubTarget(TargetKind.ISSUE, "pallets", "flask", number=42)),
            (
                f"{base}/pull/7/files",
                GitHubTarget(TargetKind.PULL_REQUEST, "pallets", "flask", number=7),
            ),
            (
                f"{base}/commit/ABCDEF1234",
                GitHubTarget(TargetKind.COMMIT, "pallets", "flask", ref="abcdef1234"),
            ),
            (
                f"{base}/releases/tag/v1.2.3",
                GitHubTarget(TargetKind.RELEASE, "pallets", "flask", ref="v1.2.3"),
            ),
            (
                f"{base}/tree/feature/x/src",
                GitHubTarget(TargetKind.TREE, "pallets", "flask", ref="feature/x/src"),
            ),
            (
                f"{base}/blob/main/README.md",
                GitHubTarget(TargetKind.BLOB, "pallets", "flask", ref="main/README.md"),
            ),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(parse_github_target(raw), expected)

    def test_malformed_known_sections_are_rejected(self) -> None:
        base = "https://github.com/pallets/flask"
        for raw in (
            f"{base}/issues/abc",
            f"{base}/pull/-1",
            f"{base}/pull/12345678901",
            f"{base}/commit/xyz",
            f"{base}/commit/abc",
            f"{base}/releases/tag/v1..;rm",
            f"{base}/tree/ma in",
            "https://evil.com/a/b/issues/1",
        ):
            with self.subTest(raw=raw), self.assertRaises(InvalidRepoReferenceError):
                parse_github_target(raw)

    def test_repo_ref_helper(self) -> None:
        target = parse_github_target("pallets/flask")
        self.assertEqual(target.repo_ref, FLASK)
        self.assertIsNone(parse_github_target("pallets").repo_ref)


if __name__ == "__main__":
    unittest.main()
