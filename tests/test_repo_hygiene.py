"""Repository-wide hygiene checks that need nothing but the standard library.

What is checked: Python line length and syntax, secrets committed by accident, the shape of
``.env.example``, that relative links in the Markdown documents resolve, and that no document claims
production readiness (the project may not make that claim until the dependencies can be installed and
the application can run).

Run from the repository root:  python3 -m unittest discover -s tests
"""

import _paths  # noqa: F401  (puts the tooling directories on sys.path)

import ast
import re
import unittest
from pathlib import Path

from _paths import ROOT

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".next", "dist", ".venv", ".mypy_cache", ".ruff_cache"}
TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".md", ".yml", ".yaml", ".sql", ".css", ".toml", ".sh"}
TEXT_NAMES = {"Dockerfile", ".dockerignore", ".gitignore", ".env.example"}
LINE_LIMIT = 120  # the limit ruff enforces for apps/api (apps/api/pyproject.toml); applied repo-wide to Python

# Shapes of real credentials. Written as fragments so that this file does not contain a match itself.
SECRET_PATTERNS = {
    "Google API key": re.compile("AI" + r"za[0-9A-Za-z_\-]{35}"),
    "GitHub token": re.compile(r"gh[pousr]" + r"_[A-Za-z0-9]{36,}"),
    "GitHub fine-grained token": re.compile("github" + r"_pat_[A-Za-z0-9_]{40,}"),
    "AWS access key id": re.compile("AK" + r"IA[0-9A-Z]{16}"),
    "Slack token": re.compile("xox" + r"[baprs]-[0-9A-Za-z\-]{10,}"),
    "private key block": re.compile("-----BEGIN (?:[A-Z]+ )?PRIVATE" + " KEY-----"),
    "OpenAI-style key": re.compile("sk" + r"-[A-Za-z0-9]{32,}"),
}


def repository_files(suffixes: set[str] = TEXT_SUFFIXES, names: set[str] = TEXT_NAMES) -> list[Path]:
    """Tracked-looking files by suffix or exact name; pass ``names=set()`` to match suffixes only."""
    found = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or SKIP_DIRS & set(path.relative_to(ROOT).parts):
            continue
        if path.suffix in suffixes or path.name in names:
            found.append(path)
    return sorted(found)


def python_files() -> list[Path]:
    return repository_files({".py"}, set())


def scan_for_secrets(text: str) -> list[str]:
    return [name for name, pattern in SECRET_PATTERNS.items() if pattern.search(text)]


READINESS = re.compile(r"production[- ]ready|ready for production|production[- ]grade", re.IGNORECASE)
DENIAL = re.compile(r"\bnot\b|\bno\b|\bnever\b|\bnor\b|\buntil\b|\bwithout\b|\bcannot\b|n't", re.IGNORECASE)


def claims_readiness(line: str) -> bool:
    """True for a line that asserts production readiness; a line that denies or conditions it is allowed."""
    return bool(READINESS.search(line)) and not DENIAL.search(line)


def text_calls_without_encoding(source: str) -> list[int]:
    """Line numbers of text reads and writes that leave the encoding to the platform.

    ``Path.read_text``, ``Path.write_text`` and the builtin ``open`` in text mode default to the locale encoding,
    which is UTF-8 on Linux and macOS but often cp1252 on Windows, where a file with a non-ASCII character then
    fails to decode. Binary opens are fine, and so are other objects' ``open`` methods (a urllib opener, say).
    """
    lines = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or any(keyword.arg == "encoding" for keyword in node.keywords):
            continue
        function = node.func
        if isinstance(function, ast.Attribute) and function.attr in {"read_text", "write_text"}:
            lines.append(node.lineno)
        elif isinstance(function, ast.Name) and function.id == "open":
            keyword_mode = next((k.value for k in node.keywords if k.arg == "mode"), None)
            mode = node.args[1] if len(node.args) > 1 else keyword_mode
            binary = isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "b" in mode.value
            if not binary:
                lines.append(node.lineno)
    return sorted(lines)


class PythonSourceTest(unittest.TestCase):
    def test_every_python_file_compiles(self) -> None:
        files = python_files()
        self.assertGreater(len(files), 40)
        for path in files:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")

    def test_every_python_file_parses_as_the_oldest_supported_python(self) -> None:
        """apps/api declares requires-python >= 3.12 but the tests run on a newer interpreter.

        Parsing with ``feature_version`` rejects syntax newer than 3.12 (it does not catch newer library calls).
        """
        for path in python_files():
            try:
                ast.parse(path.read_text(encoding="utf-8"), str(path), feature_version=(3, 12))
            except SyntaxError as error:
                self.fail(f"{path.relative_to(ROOT)} needs a newer Python than 3.12: {error}")

    def test_no_python_line_exceeds_the_limit(self) -> None:
        offenders = []
        for path in python_files():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if len(line) > LINE_LIMIT:
                    offenders.append(f"{path.relative_to(ROOT)}:{number} ({len(line)})")
        self.assertEqual(offenders, [], "lines over the limit")

    def test_no_python_file_has_tabs_or_trailing_whitespace(self) -> None:
        offenders = []
        for path in python_files():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if "\t" in line or line != line.rstrip():
                    offenders.append(f"{path.relative_to(ROOT)}:{number}")
        self.assertEqual(offenders, [])


class EncodingGuardTest(unittest.TestCase):
    """Text files are read and written as UTF-8 on purpose, so the tests and tools behave the same on Windows."""

    def test_every_text_read_and_write_names_its_encoding(self) -> None:
        offenders = []
        for path in python_files():
            for number in text_calls_without_encoding(path.read_text(encoding="utf-8")):
                offenders.append(f"{path.relative_to(ROOT)}:{number}")
        self.assertEqual(offenders, [])

    def test_the_guard_flags_a_missing_encoding(self) -> None:
        for source in (
            "p.read_text()",
            "p.write_text('x')",
            "open('f')",
            "open('f', 'w')",
            "open('f', mode='r')",
            "a.b.read_text(errors='ignore')",
        ):
            self.assertEqual(text_calls_without_encoding(source), [1], source)

    def test_the_guard_allows_what_is_safe(self) -> None:
        for source in (
            "p.read_text(encoding='utf-8')",
            "p.write_text('x', encoding='utf-8')",
            "open('f', encoding='utf-8')",
            "open('f', 'rb')",
            "open('f', mode='wb')",
            "opener.open(request)",
            "page = run.open(1440, 900)",
        ):
            self.assertEqual(text_calls_without_encoding(source), [], source)

    def test_the_guard_reports_the_right_line(self) -> None:
        self.assertEqual(text_calls_without_encoding("x = 1\n\nvalue = p.read_text()\n"), [3])


class SecretsTest(unittest.TestCase):
    def test_the_scanner_recognises_each_credential_shape(self) -> None:
        samples = {
            "Google API key": "AI" + "za" + "A" * 35,
            "GitHub token": "gh" + "p_" + "a" * 36,
            "GitHub fine-grained token": "github" + "_pat_" + "a" * 50,
            "AWS access key id": "AK" + "IA" + "A" * 16,
            "Slack token": "xox" + "b-" + "1" * 12,
            "private key block": "-----BEGIN RSA PRIVATE" + " KEY-----",
            "OpenAI-style key": "sk" + "-" + "a" * 40,
        }
        self.assertEqual(set(samples), set(SECRET_PATTERNS), "every pattern needs a positive sample")
        for name, sample in samples.items():
            self.assertEqual(scan_for_secrets(f"value = {sample}"), [name], name)

    def test_the_scanner_ignores_ordinary_text(self) -> None:
        for text in ("GEMINI_API_KEY=", "ghp_ is a prefix", "token = os.environ['GITHUB_TOKEN']", "sk-short"):
            self.assertEqual(scan_for_secrets(text), [], text)

    def test_no_file_in_the_repository_contains_a_credential(self) -> None:
        hits = []
        for path in repository_files():
            for name in scan_for_secrets(path.read_text(encoding="utf-8", errors="replace")):
                hits.append(f"{path.relative_to(ROOT)}: {name}")
        self.assertEqual(hits, [])

    def test_no_environment_file_other_than_the_example_exists_in_the_tree(self) -> None:
        stray = [
            str(p.relative_to(ROOT))
            for p in ROOT.rglob(".env*")
            if p.is_file() and p.name != ".env.example" and not SKIP_DIRS & set(p.relative_to(ROOT).parts)
        ]
        self.assertEqual(stray, [], "real environment files must never sit in the repository")

    def test_the_gitignore_excludes_environment_files_but_keeps_the_example(self) -> None:
        lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".env", lines)
        self.assertIn(".env.*", lines)
        self.assertIn("!.env.example", lines)
        self.assertLess(lines.index(".env.*"), lines.index("!.env.example"), "a later ! pattern re-includes")


class EnvExampleTest(unittest.TestCase):
    ENTRIES = {
        key: value.split("#", 1)[0].strip()
        for key, _, value in (
            line.partition("=")
            for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    }

    def test_external_service_credentials_are_empty(self) -> None:
        for key in ("GITHUB_TOKEN", "GEMINI_API_KEY", "SESSION_SECRET", "SESSION_SECRET_PREVIOUS"):
            self.assertIn(key, self.ENTRIES)
            self.assertEqual(self.ENTRIES[key], "", f"{key} must ship empty")

    def test_the_only_non_empty_passwords_are_marked_local_development_values(self) -> None:
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        for key, value in self.ENTRIES.items():
            if re.search(r"PASSWORD|SECRET|TOKEN|API_KEY", key) and value:
                line = next(line for line in text.splitlines() if line.startswith(f"{key}="))
                self.assertIn("local dev only", line, key)
                self.assertTrue(value.endswith("dev_only"), f"{key} must be an obvious placeholder")

    def test_every_variable_the_compose_file_interpolates_is_documented(self) -> None:
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        for name in sorted(set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", compose))):
            self.assertIn(name, self.ENTRIES, f"{name} is used by docker-compose.yml but missing from .env.example")

    def test_public_variables_never_hold_secrets(self) -> None:
        for key in self.ENTRIES:
            if key.startswith("NEXT_PUBLIC_"):
                self.assertNotRegex(key, r"SECRET|TOKEN|PASSWORD|KEY", f"{key} would be shipped to browsers")


class DocumentationTest(unittest.TestCase):
    LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
    FENCE = re.compile(r"```.*?```", re.DOTALL)

    @staticmethod
    def documents() -> list[Path]:
        return [p for p in repository_files({".md"}, set()) if "node_modules" not in p.parts]

    def test_relative_links_resolve(self) -> None:
        checked = 0
        for path in self.documents():
            text = self.FENCE.sub("", path.read_text(encoding="utf-8"))
            for target in self.LINK.findall(text):
                if re.match(r"[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
                    continue
                file_part = target.split("#", 1)[0]
                self.assertTrue(
                    (path.parent / file_part).resolve().exists(), f"{path.relative_to(ROOT)} links to missing {target}"
                )
                checked += 1
        self.assertGreater(checked, 0)

    def test_the_required_documents_exist(self) -> None:
        for name in ("README.md", "docs/ARCHITECTURE.md", "docs/IMPLEMENTATION_STATUS.md", "docs/FRONTEND.md",
                     "docs/COMPONENTS.md", "docs/API.md"):  # fmt: skip
            self.assertTrue((ROOT / name).is_file(), name)

    def test_nothing_claims_production_readiness(self) -> None:
        """The words may appear only to deny the claim: 'not production ready', 'until ... production ready'."""
        offenders = []
        for path in self.documents():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if claims_readiness(line):
                    offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()[:100]}")
        self.assertEqual(offenders, [])

    def test_status_document_labels_unverified_work(self) -> None:
        text = (ROOT / "docs" / "IMPLEMENTATION_STATUS.md").read_text(encoding="utf-8")
        self.assertIn("UNVERIFIED", text)
        for heading in ("Verified", "Unverified"):
            self.assertRegex(text, rf"(?im)^#+\s.*{heading}", heading)


class ClaimGuardTest(unittest.TestCase):
    """The production-readiness guard must itself reject a plain claim and allow a denial."""

    def test_plain_claims_are_flagged(self) -> None:
        for line in (
            "VYRON is production ready.",
            "It is ready for production.",
            "A production-grade platform.",
            "Status: PRODUCTION READY",
        ):
            self.assertTrue(claims_readiness(line), line)

    def test_denials_and_conditions_are_allowed(self) -> None:
        for line in (
            "VYRON is not production ready.",
            "Never call it production-ready.",
            "No claim of production-grade quality is made.",
            "It stays unverified until it is production ready.",
            "We can't say it is ready for production.",
        ):
            self.assertFalse(claims_readiness(line), line)

    def test_unrelated_lines_are_ignored(self) -> None:
        self.assertFalse(claims_readiness("A stable interface."))


if __name__ == "__main__":
    unittest.main()
