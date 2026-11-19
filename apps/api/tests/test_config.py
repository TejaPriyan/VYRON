import ast
import re
import unittest
from pathlib import Path

from vyron_api.config import (
    MIN_SECRET_LENGTH,
    AppEnv,
    ConfigError,
    DataMode,
    Secret,
    derive_secret,
    load_settings,
)

LONG = "s" * 40
OTHER = "o" * 40
PROD_DB = {
    "APP_ENV": "production",
    "SESSION_SECRET": LONG,
    "DATABASE_URL": "postgresql://vyron:hunter2hunter2@db.internal:5432/vyron",
    "NEO4J_URI": "neo4j+s://graph.internal:7687",
    "NEO4J_USERNAME": "neo4j",
    "NEO4J_PASSWORD": "graphpass-hunter3",
    "REDIS_URL": "rediss://:redispass-hunter4@cache.internal:6379/0",
}


def problems_of(environ: dict[str, str]) -> str:
    with unittest.TestCase().assertRaises(ConfigError) as caught:
        load_settings(environ)
    return str(caught.exception)


class DevelopmentDefaultsTest(unittest.TestCase):
    def test_empty_environment_is_a_valid_development_setup(self) -> None:
        settings = load_settings({})
        self.assertEqual(settings.app_env, AppEnv.DEVELOPMENT)
        self.assertEqual(settings.data_mode, DataMode.SEED)
        self.assertEqual(settings.log_level, "info")
        self.assertEqual(settings.trusted_proxy_hops, 0)
        self.assertFalse(settings.ai_enabled)
        self.assertIsNone(settings.cursor_secrets())  # a throwaway key is used instead

    def test_blank_values_count_as_unset(self) -> None:
        settings = load_settings({"GITHUB_TOKEN": "  ", "GEMINI_API_KEY": "", "SESSION_SECRET": ""})
        self.assertIsNone(settings.github_token)
        self.assertIsNone(settings.session_secret)


class ProductionRulesTest(unittest.TestCase):
    def test_valid_production_configuration(self) -> None:
        settings = load_settings(PROD_DB)
        self.assertTrue(settings.is_production)
        self.assertEqual(settings.data_mode, DataMode.DATABASE)  # the production default

    def test_production_requires_a_strong_stable_secret(self) -> None:
        without = {k: v for k, v in PROD_DB.items() if k != "SESSION_SECRET"}
        self.assertIn("SESSION_SECRET is required", problems_of(without))
        self.assertIn("at least", problems_of({**PROD_DB, "SESSION_SECRET": "short"}))

    def test_production_refuses_seed_data_unless_explicitly_allowed(self) -> None:
        base = {"APP_ENV": "production", "SESSION_SECRET": LONG, "DATA_MODE": "seed"}
        self.assertIn("refuses DATA_MODE=seed", problems_of(base))
        self.assertEqual(load_settings({**base, "ALLOW_SEED_DATA": "true"}).data_mode, DataMode.SEED)

    def test_database_mode_requires_all_three_stores(self) -> None:
        for name in ("DATABASE_URL", "NEO4J_URI", "REDIS_URL", "NEO4J_PASSWORD"):
            with self.subTest(missing=name):
                env = {k: v for k, v in PROD_DB.items() if k != name}
                self.assertIn(f"{name} is required", problems_of(env))

    def test_url_schemes_are_checked(self) -> None:
        self.assertIn("DATABASE_URL must start with", problems_of({**PROD_DB, "DATABASE_URL": "mysql://x"}))
        self.assertIn("REDIS_URL must start with", problems_of({**PROD_DB, "REDIS_URL": "http://x"}))
        self.assertIn("NEO4J_URI must start with", problems_of({**PROD_DB, "NEO4J_URI": "http://x"}))

    def test_invalid_app_env_is_reported_not_silently_relaxed(self) -> None:
        self.assertIn("APP_ENV must be one of", problems_of({"APP_ENV": "prod"}))


class SecretHandlingTest(unittest.TestCase):
    def test_secrets_never_print(self) -> None:
        settings = load_settings({**PROD_DB, "GITHUB_TOKEN": "ghp_" + "x" * 36})
        rendered = " ".join([repr(settings), str(settings), f"{settings}", repr(settings.database_url)])
        rendered += f"{settings.github_token} {settings.session_secret!s} {settings.redis_url:>20}"
        for secret in ("hunter2", "hunter3", "hunter4", "ghp_", LONG):
            self.assertNotIn(secret, rendered)

    def test_reveal_is_the_only_way_out(self) -> None:
        self.assertEqual(Secret("abc").reveal(), "abc")
        self.assertEqual(repr(Secret("abc")), "Secret('***')")
        self.assertTrue(Secret("abc"))
        self.assertFalse(Secret(""))
        self.assertEqual(Secret("abc"), Secret("abc"))
        self.assertNotEqual(Secret("abc"), "abc")
        with self.assertRaises(TypeError):
            hash(Secret("abc"))

    def test_safe_summary_has_no_values(self) -> None:
        summary = load_settings(PROD_DB).safe_summary()
        self.assertEqual(summary["session_secret"], "set")
        self.assertNotIn("hunter", repr(summary))
        self.assertNotIn(LONG, repr(summary))

    def test_error_messages_never_contain_values(self) -> None:
        env = {
            **PROD_DB,
            "DATABASE_URL": "mysql://admin:hunter2hunter2@db",
            "SESSION_SECRET": "tooshort-hunter9",
            "REDIS_URL": "http://:redispass-hunter4@x",
            "GEMINI_API_KEY": "AIza-hunter5",
        }
        message = problems_of(env)
        for secret in ("hunter2", "hunter9", "hunter4", "hunter5", "admin"):
            self.assertNotIn(secret, message)

    def test_all_problems_are_reported_together(self) -> None:
        message = problems_of({"APP_ENV": "production", "LOG_LEVEL": "loud", "TRUSTED_PROXY_HOPS": "x"})
        for fragment in ("SESSION_SECRET is required", "LOG_LEVEL must be", "TRUSTED_PROXY_HOPS must be"):
            self.assertIn(fragment, message)


class ValidationOrderTest(unittest.TestCase):
    """Every check must run before the error is raised, or a problem it finds is silently dropped."""

    @staticmethod
    def load_settings_ast() -> ast.FunctionDef:
        source = (Path(__file__).resolve().parents[1] / "src" / "vyron_api" / "config.py").read_text(encoding="utf-8")
        for node in ast.parse(source).body:
            if isinstance(node, ast.FunctionDef) and node.name == "load_settings":
                return node
        raise AssertionError("load_settings not found")

    def test_nothing_is_validated_after_the_problems_check(self) -> None:
        body = self.load_settings_ast().body
        guards = [
            i
            for i, stmt in enumerate(body)
            if isinstance(stmt, ast.If)
            and isinstance(stmt.test, ast.Name)
            and stmt.test.id == "problems"
            and any(isinstance(s, ast.Raise) for s in stmt.body)
        ]
        self.assertEqual(len(guards), 1, "expected exactly one `if problems: raise` in load_settings")
        late_calls = [
            ast.unparse(call)
            for stmt in body[guards[0] + 1 :]
            for call in ast.walk(stmt)
            if isinstance(call, ast.Call) and any(isinstance(a, ast.Name) and a.id == "problems" for a in call.args)
        ]
        self.assertEqual(late_calls, [], "these checks run after the error was already decided")

    def test_the_guard_would_catch_the_original_mistake(self) -> None:
        late = ast.parse(
            "def f(environ):\n"
            "    problems = []\n"
            "    if problems:\n"
            "        raise ValueError\n"
            "    return S(token=_secret(environ, 'T', problems))\n"
        ).body[0]
        assert isinstance(late, ast.FunctionDef)
        calls = [
            c for stmt in late.body[2:] for c in ast.walk(stmt)
            if isinstance(c, ast.Call) and any(isinstance(a, ast.Name) and a.id == "problems" for a in c.args)
        ]  # fmt: skip
        self.assertEqual(len(calls), 1)


class CursorKeyDerivationTest(unittest.TestCase):
    def test_keys_are_purpose_separated_and_stable(self) -> None:
        master = Secret(LONG)
        self.assertEqual(derive_secret(master, "cursor"), derive_secret(master, "cursor"))
        self.assertNotEqual(derive_secret(master, "cursor"), derive_secret(master, "csrf"))
        self.assertNotEqual(derive_secret(master, "cursor"), derive_secret(Secret(OTHER), "cursor"))
        self.assertGreaterEqual(len(derive_secret(master, "cursor")), MIN_SECRET_LENGTH)
        self.assertNotIn(LONG.encode(), derive_secret(master, "cursor"))

    def test_rotation_lists_current_key_first(self) -> None:
        settings = load_settings({"SESSION_SECRET": LONG, "SESSION_SECRET_PREVIOUS": OTHER})
        keys = settings.cursor_secrets()
        self.assertEqual(keys, [derive_secret(Secret(LONG), "cursor"), derive_secret(Secret(OTHER), "cursor")])

    def test_rotation_rules(self) -> None:
        self.assertIn("needs SESSION_SECRET", problems_of({"SESSION_SECRET_PREVIOUS": OTHER}))
        self.assertIn("must differ", problems_of({"SESSION_SECRET": LONG, "SESSION_SECRET_PREVIOUS": LONG}))


class OtherSettingsTest(unittest.TestCase):
    def test_trusted_proxy_hops(self) -> None:
        self.assertEqual(load_settings({"TRUSTED_PROXY_HOPS": "2"}).trusted_proxy_hops, 2)
        for bad in ("-1", "6", "two", "1.5", "100"):
            with self.subTest(bad=bad):
                self.assertIn("TRUSTED_PROXY_HOPS", problems_of({"TRUSTED_PROXY_HOPS": bad}))

    def test_gemini_requires_an_explicit_model_and_is_otherwise_optional(self) -> None:
        self.assertIn("GEMINI_MODEL must be set", problems_of({"GEMINI_API_KEY": "k"}))
        enabled = load_settings({"GEMINI_API_KEY": "k", "GEMINI_MODEL": "some-model-id-1.5"})
        self.assertTrue(enabled.ai_enabled)
        self.assertFalse(load_settings({"GEMINI_MODEL": "some-model-id"}).ai_enabled)  # no key, no AI
        self.assertIn("not a valid model id", problems_of({"GEMINI_MODEL": "bad model; rm -rf"}))

    def test_flag_parsing(self) -> None:
        for value in ("true", "TRUE", "1", "yes"):
            self.assertEqual(load_settings({"ALLOW_SEED_DATA": value}).data_mode, DataMode.SEED)
        self.assertIn("ALLOW_SEED_DATA must be true or false", problems_of({"ALLOW_SEED_DATA": "maybe"}))


ROOT = Path(__file__).resolve().parents[3]


def parse_env_file(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = re.sub(r"\s+#.*$", "", value).strip()
    return values


class EnvExampleTest(unittest.TestCase):
    example = parse_env_file((ROOT / ".env.example").read_text(encoding="utf-8"))

    def test_every_variable_the_code_reads_is_documented(self) -> None:
        source = (ROOT / "apps/api/src/vyron_api/config.py").read_text(encoding="utf-8")
        names = set(re.findall(r'"([A-Z][A-Z0-9_]{3,})"', source))
        self.assertGreater(len(names), 12)
        self.assertEqual(sorted(names - set(self.example)), [])

    def test_the_example_is_a_valid_development_configuration(self) -> None:
        settings = load_settings(self.example)
        self.assertEqual(settings.app_env, AppEnv.DEVELOPMENT)
        self.assertEqual(settings.data_mode, DataMode.SEED)

    def test_the_example_contains_no_real_secrets(self) -> None:
        for name in ("SESSION_SECRET", "SESSION_SECRET_PREVIOUS", "GITHUB_TOKEN", "GEMINI_API_KEY"):
            self.assertEqual(self.example[name], "", name)
        for name, value in self.example.items():
            if "PASSWORD" in name:
                self.assertIn("dev_only", value, name)  # clearly local-development placeholders


class BootstrapTest(unittest.TestCase):
    def test_seed_mode_builds_an_application(self) -> None:
        from vyron_api.api.bootstrap import build_application

        self.assertIsNotNone(build_application(load_settings({})))

    def test_the_configured_github_token_reaches_the_live_provider_and_nothing_else(self) -> None:
        from unittest import mock

        from vyron_api.api.bootstrap import build_application

        secret = "ghp_NotARealToken0123456789abcdefghij"
        for environ, expected in (({"GITHUB_TOKEN": secret}, secret), ({}, None)):
            with self.subTest(set=bool(environ)), mock.patch("vyron_api.api.bootstrap.build_live_provider") as build:
                application = build_application(load_settings(environ))
            build.assert_called_once_with(token=expected)
            self.assertNotIn(secret, repr(application))

    def test_database_mode_fails_loudly_instead_of_silently_using_seed_data(self) -> None:
        from vyron_api.api.bootstrap import AdapterNotImplementedError, build_application

        with self.assertRaises(AdapterNotImplementedError):
            build_application(load_settings(PROD_DB))


if __name__ == "__main__":
    unittest.main()
