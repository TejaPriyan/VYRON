"""Static checks of the Docker configuration, and the parts of it that can run on the host.

NOTHING HERE BUILDS AN IMAGE. The Docker daemon was not available, so every image and container
claim is UNVERIFIED. What is checked: Dockerfile structure and hardening rules (dockerlint),
.dockerignore against the COPY sources, the compose file as resolved by `docker compose config`
(which needs the CLI but no daemon), and the exact commands the seed-dev image would run, executed
on the host.

Run from the repository root:  python3 -m unittest discover -s tests
"""

import _paths  # noqa: F401  (puts the tooling directories on sys.path)

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

from _server import start_dev_server, stop_server
from dockerlint import DockerfileError, is_ignored, lint, parse, parse_dockerignore, stages_of

ROOT = Path(__file__).resolve().parents[1]
API_DOCKERFILE = ROOT / "apps" / "api" / "Dockerfile"
WEB_DOCKERFILE = ROOT / "apps" / "web" / "Dockerfile"
COMPOSE = ROOT / "docker-compose.yml"
DOCKER = shutil.which("docker")

# COPY sources of the web template that do not exist because the Next.js app is not written.
# When apps/web gains its files this set shrinks; it may only ever be a subset of what is missing.
WEB_PENDING = frozenset(
    {
        "package.json",
        "package-lock.json",
        "apps/web/package.json",
        "packages/types/package.json",
        "packages/api-client/package.json",
        "packages/graph/package.json",
        "packages/ui/package.json",
        "apps/web",
        "packages",
    }
)

# Files under a COPY source that .dockerignore deliberately keeps out of an image.
INTENDED_EXCLUSIONS = re.compile(r"(\.test\.ts$|typecheck\.fixture\.ts$|__pycache__|node_modules|/\.next/)")

GOOD = """\
ARG IMG=python:3.13-slim
FROM ${IMG} AS base
ENV PIP_NO_CACHE_DIR=1
RUN useradd --uid 10001 app
WORKDIR /app
FROM base AS run
USER 10001:10001
EXPOSE 8000
HEALTHCHECK CMD ["true"]
CMD ["python", "-V"]
"""


def run(command: list[str], timeout: float = 30, **kwargs: object) -> subprocess.CompletedProcess[str]:
    """Run a command to completion, capturing text output; a hang fails the test instead of the suite."""
    options: dict[str, object] = {"capture_output": True, "text": True, "timeout": timeout, "check": False}
    return subprocess.run(command, **{**options, **kwargs})  # type: ignore[call-overload]


def host_env(**extra: str) -> dict[str, str]:
    """A minimal environment for running image commands on the host.

    On Windows Python cannot initialise sockets without ``SYSTEMROOT``; keeping it there is UNVERIFIED (this
    suite has only run on Linux).
    """
    env = {"PATH": os.environ.get("PATH", "")}
    if sys.platform == "win32":
        env.update({k: v for k, v in os.environ.items() if k.upper() in ("SYSTEMROOT", "WINDIR")})
    return {**env, **extra}


def rules(text: str, **kwargs: object) -> list[str]:
    return sorted({f.rule for f in lint(text, context=ROOT, **kwargs)})  # type: ignore[arg-type]


class ParserTest(unittest.TestCase):
    def test_joins_continuations_and_skips_comments(self) -> None:
        text = "# comment\nRUN a \\\n  # inner comment\n  && b \\\n  && c\nENV X=1\n"
        instructions = parse(text)
        self.assertEqual([i.name for i in instructions], ["RUN", "ENV"])
        self.assertEqual(instructions[0].args.split(), ["a", "&&", "b", "&&", "c"])
        self.assertEqual(instructions[0].line, 2)

    def test_rejects_what_it_cannot_check(self) -> None:
        with self.assertRaises(DockerfileError):
            parse("RUN cat <<EOF\nhi\nEOF\n")
        with self.assertRaises(DockerfileError):
            parse("FROM x\nBOGUS y\n")
        with self.assertRaises(DockerfileError):
            parse("RUN a \\\n")

    def test_arg_before_from_resolves_the_image(self) -> None:
        args, stages = stages_of(parse(GOOD))
        self.assertEqual(args, {"IMG": "python:3.13-slim"})
        self.assertEqual([s.name for s in stages], ["base", "run"])
        self.assertEqual(stages[0].image, "python:3.13-slim")
        self.assertIs(stages[1].parent, stages[0])

    def test_instruction_before_from_is_an_error(self) -> None:
        with self.assertRaises(DockerfileError):
            stages_of(parse("RUN x\nFROM a:1\n"))


class RuleTest(unittest.TestCase):
    def test_a_minimal_good_file_has_no_findings(self) -> None:
        self.assertEqual(rules(GOOD), [])

    def test_each_rule_fires_on_its_own_violation(self) -> None:
        cases = {
            "image-pin": GOOD.replace("python:3.13-slim", "python:latest"),
            "image-pin ": GOOD.replace("ARG IMG=python:3.13-slim", "ARG IMG=python"),
            "no-user": GOOD.replace("USER 10001:10001\n", ""),
            "root-user": GOOD.replace("USER 10001:10001", "USER root"),
            "named-user": GOOD.replace("USER 10001:10001", "USER app"),
            "no-healthcheck": GOOD.replace('HEALTHCHECK CMD ["true"]\n', ""),
            "shell-form": GOOD.replace('CMD ["python", "-V"]', "CMD python -V"),
            "remote-add": GOOD + "ADD https://example.com/x.tgz /x\n",
            "pipe-to-shell": GOOD.replace("WORKDIR /app", "RUN curl -fsSL https://x.sh | sh"),
            "secret-in-env": GOOD.replace("WORKDIR /app", "ENV API_KEY=abc123"),
            "secret-in-arg": GOOD.replace("ARG IMG", "ARG DB_PASSWORD=hunter2\nARG IMG"),
            "relative-workdir": GOOD.replace("WORKDIR /app", "WORKDIR app"),
            "copy-from": GOOD.replace("WORKDIR /app", "COPY --from=ghost /a /b"),
            "copy-source-missing": GOOD.replace("WORKDIR /app", "COPY nope.txt /app/"),
            "chmod-777": GOOD.replace("WORKDIR /app", "RUN chmod 777 /app"),
            "apt-recommends": GOOD.replace("WORKDIR /app", "RUN apt-get install -y curl"),
            "expose": GOOD.replace("EXPOSE 8000", "EXPOSE http"),
            "sudo": GOOD.replace("WORKDIR /app", "RUN sudo ls"),
            "duplicate-stage": GOOD.replace("AS run", "AS base"),
        }
        for rule, text in cases.items():
            self.assertIn(rule.strip(), rules(text), rule)

    def test_pip_cache_rule_respects_the_environment_variable(self) -> None:
        bare = GOOD.replace("ENV PIP_NO_CACHE_DIR=1\n", "").replace("WORKDIR /app", "RUN pip install x")
        self.assertIn("pip-cache", rules(bare))
        self.assertNotIn("pip-cache", rules(bare.replace("pip install x", "pip install --no-cache-dir x")))
        self.assertNotIn("pip-cache", rules(GOOD.replace("WORKDIR /app", "RUN pip install x")))

    def test_a_digest_pins_the_image_without_a_tag(self) -> None:
        pinned = GOOD.replace("python:3.13-slim", "python@sha256:" + "a" * 64)
        self.assertEqual(rules(pinned), [])

    def test_secret_references_to_build_args_are_not_literals(self) -> None:
        self.assertNotIn("secret-in-env", rules(GOOD.replace("WORKDIR /app", "ARG TOKEN\nENV TOKEN=$TOKEN")))

    def test_user_is_inherited_from_a_parent_stage_and_overridden_by_a_child(self) -> None:
        text = GOOD.replace("USER 10001:10001\n", "")
        text = text.replace("RUN useradd --uid 10001 app", "RUN useradd --uid 10001 app\nUSER 10001")
        self.assertNotIn("no-user", rules(text))
        self.assertIn("root-user", rules(text.replace("FROM base AS run", "FROM base AS run\nUSER root")))

    def test_existing_sources_pass_and_pending_sources_can_be_allowed(self) -> None:
        text = GOOD.replace("WORKDIR /app", "COPY apps/api/pyproject.toml /x")
        self.assertEqual(rules(text), [])
        missing = GOOD.replace("WORKDIR /app", "COPY future.json /x")
        self.assertIn("copy-source-missing", rules(missing))
        self.assertEqual(rules(missing, pending_sources=frozenset({"future.json"})), [])


class RepositoryDockerfilesTest(unittest.TestCase):
    def test_api_dockerfile_passes_every_rule(self) -> None:
        findings = lint(API_DOCKERFILE.read_text(encoding="utf-8"), context=ROOT)
        self.assertEqual(findings, [], findings)

    def test_api_dockerfile_declares_both_targets_and_that_it_is_unverified(self) -> None:
        text = API_DOCKERFILE.read_text(encoding="utf-8")
        _, stages = stages_of(parse(text))
        self.assertTrue({"seed-dev", "runtime"} <= {s.name for s in stages})
        self.assertIn("STATUS: UNVERIFIED", text)
        self.assertNotRegex(text, r"@sha256:[0-9a-f]{64}\b(?!.*<digest>)")  # no invented digests

    def test_web_template_declares_itself_unverified(self) -> None:
        self.assertIn("UNVERIFIED TEMPLATE", WEB_DOCKERFILE.read_text(encoding="utf-8"))

    def test_web_template_fails_only_on_the_missing_next_app(self) -> None:
        findings = lint(WEB_DOCKERFILE.read_text(encoding="utf-8"), context=ROOT)
        other = [f for f in findings if f.rule != "copy-source-missing"]
        self.assertEqual(other, [], other)
        missing = {re.search(r"'([^']+)'", f.message).group(1) for f in findings}  # type: ignore[union-attr]
        self.assertTrue(missing <= WEB_PENDING, f"unexpected missing sources: {sorted(missing - WEB_PENDING)}")

    def test_web_template_passes_clean_once_the_app_exists(self) -> None:
        text = WEB_DOCKERFILE.read_text(encoding="utf-8")
        self.assertEqual([f for f in lint(text, context=ROOT, pending_sources=WEB_PENDING)], [])

    def test_runtime_images_run_as_the_documented_uid(self) -> None:
        for path in (API_DOCKERFILE, WEB_DOCKERFILE):
            self.assertIn("USER 10001:10001", path.read_text(encoding="utf-8"), path.name)

    def test_no_secret_names_carry_values_in_either_dockerfile(self) -> None:
        for path in (API_DOCKERFILE, WEB_DOCKERFILE):
            for finding in lint(path.read_text(encoding="utf-8"), context=ROOT, pending_sources=WEB_PENDING):
                self.assertNotIn("secret", finding.rule, f"{path}: {finding}")


class DockerignoreTest(unittest.TestCase):
    RULES = parse_dockerignore((ROOT / ".dockerignore").read_text(encoding="utf-8"))

    def test_secrets_and_vcs_are_excluded(self) -> None:
        for path in (
            ".env", ".env.local", ".git/config", "keys/server.pem", "deploy/id_rsa", "x/service-account-1.json",
            "apps/api/.env", "apps/web/.env.local", "apps/api/certs/tls.key", "apps/api/server.pem",
            "infra/credentials-prod.json", "a/b/c/bundle.p12", "a/b/store.pfx", "a/b/.ssh/id_rsa.pub",
        ):  # fmt: skip
            self.assertTrue(is_ignored(path, self.RULES), path)

    def test_the_example_env_file_is_allowed_through(self) -> None:
        self.assertFalse(is_ignored(".env.example", self.RULES))
        self.assertFalse(is_ignored("apps/api/.env.example", self.RULES))

    def test_dependencies_and_build_output_are_excluded(self) -> None:
        for path in ("node_modules/x/index.js", "apps/web/node_modules/y.js", "apps/web/.next/cache/a",
                     "apps/api/src/vyron_api/__pycache__/a.pyc", ".venv/bin/python"):  # fmt: skip
            self.assertTrue(is_ignored(path, self.RULES), path)

    def test_every_copied_source_file_survives_the_ignore_file(self) -> None:
        checked = 0
        for dockerfile in (API_DOCKERFILE, WEB_DOCKERFILE):
            _, stages = stages_of(parse(dockerfile.read_text(encoding="utf-8")))
            for stage in stages:
                for ins in stage.instructions:
                    if ins.name != "COPY" or "--from=" in ins.args:
                        continue
                    sources = [p for p in ins.args.split() if not p.startswith("--")][:-1]
                    for source in sources:
                        target = ROOT / source
                        if not target.exists():
                            continue  # pending web sources are reported by their own test
                        files = [target] if target.is_file() else [p for p in target.rglob("*") if p.is_file()]
                        for file in files:
                            relative = file.relative_to(ROOT).as_posix()
                            if is_ignored(relative, self.RULES):
                                # Tests, type-check fixtures and caches are meant to stay out of the image.
                                message = f"{dockerfile.name}: {relative} is ignored unexpectedly"
                                self.assertRegex(relative, INTENDED_EXCLUSIONS, message)
                                continue
                            checked += 1
        self.assertGreater(checked, 30)

    def test_matcher_semantics(self) -> None:
        rules_ = parse_dockerignore("docs\n**/tests\n*.md\n!keep.md\n/only-root\n**/*.test.ts\n")
        self.assertTrue(is_ignored("docs/a/b.txt", rules_))
        self.assertTrue(is_ignored("x/y/tests/z.py", rules_))
        self.assertTrue(is_ignored("README.md", rules_))
        self.assertFalse(is_ignored("keep.md", rules_), "a later ! pattern re-includes")
        self.assertFalse(is_ignored("sub/README.md", rules_), "*.md without ** matches only at the root")
        self.assertTrue(is_ignored("only-root", rules_))
        self.assertTrue(is_ignored("a/b/c.test.ts", rules_))
        self.assertFalse(is_ignored("a/b/c.ts", rules_))


@unittest.skipUnless(DOCKER, "the docker CLI is not on PATH")
class ComposeTest(unittest.TestCase):
    """`docker compose config` resolves and validates the file; it does not need a running daemon."""

    @classmethod
    def resolve(cls, *profiles: str) -> dict:
        assert DOCKER is not None
        env = {**os.environ, "POSTGRES_PASSWORD": "pw", "NEO4J_PASSWORD": "neo4jpass1"}
        args = [DOCKER, "compose", "-f", str(COMPOSE)]
        for profile in profiles:
            args += ["--profile", profile]
        result = run([*args, "config", "--format", "json"], timeout=60, env=env)
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        return json.loads(result.stdout)

    def test_each_profile_resolves(self) -> None:
        self.assertEqual(sorted(self.resolve()["services"]), ["neo4j", "postgres", "redis"])
        self.assertEqual(sorted(self.resolve("seed")["services"]), ["api-seed", "neo4j", "postgres", "redis"])
        self.assertEqual(sorted(self.resolve("app")["services"]), ["api", "neo4j", "postgres", "redis", "web"])

    def test_required_passwords_are_enforced(self) -> None:
        assert DOCKER is not None
        env = {k: v for k, v in os.environ.items() if k not in ("POSTGRES_PASSWORD", "NEO4J_PASSWORD")}
        result = run([DOCKER, "compose", "-f", str(COMPOSE), "config", "--quiet"], timeout=60, env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("required variable", result.stderr)

    def test_every_published_port_is_bound_to_loopback(self) -> None:
        for name, service in self.resolve("seed", "app")["services"].items():
            for port in service.get("ports", []):
                self.assertEqual(port.get("host_ip"), "127.0.0.1", f"{name}: {port}")

    def test_application_services_are_hardened(self) -> None:
        services = self.resolve("seed", "app")["services"]
        for name in ("api-seed", "api", "web"):
            service = services[name]
            self.assertTrue(service.get("read_only"), name)
            self.assertEqual(service.get("cap_drop"), ["ALL"], name)
            self.assertIn("no-new-privileges:true", service.get("security_opt", []), name)
            self.assertGreater(service.get("pids_limit", 0), 0, name)
            self.assertGreater(int(service.get("mem_limit", 0)), 0, name)
            self.assertFalse(service.get("privileged"), name)
            self.assertNotEqual(service.get("network_mode"), "host", name)
            self.assertFalse([v for v in service.get("volumes", []) if "docker.sock" in json.dumps(v)], name)
            self.assertIn("tmpfs", service, f"{name}: a read-only root needs a writable /tmp")

    def test_application_services_are_opt_in(self) -> None:
        services = self.resolve("seed", "app")["services"]
        for name in ("postgres", "neo4j", "redis"):
            self.assertNotIn("profiles", services[name], name)
        self.assertEqual(services["api-seed"]["profiles"], ["seed"])
        self.assertEqual(services["api"]["profiles"], ["app"])
        self.assertEqual(services["web"]["profiles"], ["app"])

    def test_the_two_api_services_cannot_run_together_on_one_port(self) -> None:
        services = self.resolve("seed", "app")["services"]
        published = {name: {p["published"] for p in services[name]["ports"]} for name in ("api", "api-seed")}
        shared = published["api"] & published["api-seed"]
        self.assertTrue(shared, "if the ports ever differ this guard can be relaxed")
        self.assertNotEqual(services["api"]["profiles"], services["api-seed"]["profiles"])
        self.assertIn("Do not enable both", COMPOSE.read_text(encoding="utf-8"))

    def test_build_targets_exist_in_the_dockerfile(self) -> None:
        _, stages = stages_of(parse(API_DOCKERFILE.read_text(encoding="utf-8")))
        names = {s.name for s in stages}
        services = self.resolve("seed", "app")["services"]
        for name in ("api-seed", "api"):
            self.assertIn(services[name]["build"]["target"], names, name)
            self.assertEqual(services[name]["build"]["dockerfile"], "apps/api/Dockerfile", name)

    def test_the_seed_service_needs_no_secrets_and_no_database(self) -> None:
        seed = self.resolve("seed")["services"]["api-seed"]
        self.assertNotIn("depends_on", seed)
        self.assertEqual(seed["environment"]["DATA_MODE"], "seed")
        for key in seed["environment"]:
            self.assertNotRegex(key, r"(?i)password|secret|token|api_?key")

    def test_the_full_stack_service_is_wired_to_the_stores_by_service_name(self) -> None:
        api = self.resolve("app")["services"]["api"]
        self.assertEqual(api["environment"]["DATA_MODE"], "database")
        self.assertIn("@postgres:5432/", api["environment"]["DATABASE_URL"])
        self.assertEqual(api["environment"]["NEO4J_URI"], "bolt://neo4j:7687")
        self.assertEqual(api["environment"]["REDIS_URL"], "redis://redis:6379/0")
        self.assertEqual(sorted(api["depends_on"]), ["neo4j", "postgres", "redis"])
        for dependency in api["depends_on"].values():
            self.assertEqual(dependency["condition"], "service_healthy")

    def test_secrets_are_interpolated_never_written_in_the_file(self) -> None:
        for number, line in enumerate(COMPOSE.read_text(encoding="utf-8").splitlines(), start=1):
            match = re.match(r"\s+([A-Z0-9_]*(?:PASSWORD|SECRET|TOKEN|API_KEY)[A-Z0-9_]*):\s*(.*)$", line)
            if match:
                self.assertRegex(match.group(2), r"^\$\{|^neo4j/\$\{", f"line {number}: {line.strip()}")

    def test_every_interpolated_variable_is_documented_in_env_example(self) -> None:
        text = COMPOSE.read_text(encoding="utf-8")
        used = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", text.replace("$${", "")))
        documented = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", (ROOT / ".env.example").read_text(encoding="utf-8"), re.M))
        self.assertEqual(used - documented, set())
        self.assertGreater(len(used), 8)

    def test_the_exposed_port_matches_the_published_one_and_the_healthcheck(self) -> None:
        dockerfile = API_DOCKERFILE.read_text(encoding="utf-8")
        self.assertEqual(dockerfile.count("EXPOSE 8000"), 2)
        self.assertEqual(dockerfile.count("http://127.0.0.1:8000/health/live"), 2)
        for service in self.resolve("seed", "app")["services"].values():
            for port in service.get("ports", []):
                if port["published"] == "8000":
                    self.assertEqual(port["target"], 8000)


class SeedDevImageCommandTest(unittest.TestCase):
    """Run, on the host, the command and healthcheck the seed-dev image uses."""

    @staticmethod
    def image_settings() -> tuple[list[str], list[str], dict[str, str]]:
        """(CMD, HEALTHCHECK command, ENV) of the seed-dev stage, parsed from the Dockerfile itself."""
        _, stages = stages_of(parse(API_DOCKERFILE.read_text(encoding="utf-8")))
        chain = next(s for s in stages if s.name == "seed-dev").chain()
        cmd = json.loads(next(i for i in chain if i.name == "CMD").args)
        health_args = next(i for i in chain if i.name == "HEALTHCHECK").args
        health = json.loads(health_args[health_args.index("CMD") + 3 :].strip())
        env: dict[str, str] = {}
        for ins in chain:
            if ins.name == "ENV":
                env.update(re.findall(r"(\w+)=(\S+)", ins.args))
        return cmd, health, env

    def test_the_dockerfile_commands_parse_as_expected(self) -> None:
        cmd, health, env = self.image_settings()
        self.assertEqual(cmd[:3], ["python", "-m", "vyron_api.devserver"])
        self.assertEqual(cmd[cmd.index("--host") + 1], "0.0.0.0")
        self.assertEqual(health[:2], ["python", "-c"])
        self.assertEqual(env["DATA_MODE"], "seed")
        self.assertEqual(env["APP_ENV"], "development")
        self.assertEqual(env["PYTHONUNBUFFERED"], "1")

    def test_container_command_serves_and_the_healthcheck_command_passes(self) -> None:
        cmd, health, image_env = self.image_settings()
        host_cmd = [sys.executable if part == "python" else part for part in cmd]
        host_cmd[host_cmd.index("--host") + 1] = "127.0.0.1"  # 0.0.0.0 inside the container
        host_cmd[host_cmd.index("--port") + 1] = "0"
        # The image's own environment, with only the source path pointed at this checkout.
        env = host_env(**{**image_env, "PYTHONPATH": str(ROOT / "apps" / "api" / "src")})
        before = self.snapshot()
        with tempfile.TemporaryDirectory() as scratch:
            server = start_dev_server(host_cmd, env, cwd=scratch)
            try:
                probe = health[-1].replace("127.0.0.1:8000", f"127.0.0.1:{server.port}")
                self.assertNotEqual(probe, health[-1], "the healthcheck must target the port the image exposes")
                result = run([sys.executable, *health[1:-1], probe])
                self.assertEqual(result.returncode, 0, result.stderr)
                meta_url = f"{server.url}/api/v1/meta"
                with urllib.request.urlopen(meta_url, timeout=5) as response:  # noqa: S310 (loopback)
                    meta = json.loads(response.read())
                self.assertEqual(meta["data_mode"], "seed")
                self.assertIn("seed", (meta["data_notice"] or "").lower())
            finally:
                stop_server(server.process)
            self.assertEqual(os.listdir(scratch), [], "the server wrote files into its working directory")
        self.assertEqual(
            self.snapshot(), before, "the server wrote files into the source tree (a read-only root would fail)"
        )

    def test_a_healthcheck_against_a_dead_port_fails(self) -> None:
        _, health, _ = self.image_settings()
        probe = health[-1].replace("127.0.0.1:8000", "127.0.0.1:9")  # the discard port: nothing listens
        result = run([sys.executable, *health[1:-1], probe])
        self.assertNotEqual(result.returncode, 0)

    def test_the_server_refuses_to_start_when_told_it_is_production(self) -> None:
        env = host_env(PYTHONPATH=str(ROOT / "apps" / "api" / "src"), APP_ENV="production", DATA_MODE="seed")
        cmd = [sys.executable, "-c", "from vyron_api.config import load_settings; import os; load_settings(os.environ)"]
        result = run(cmd, env=env)
        self.assertNotEqual(result.returncode, 0, "production must not accept seed data without ALLOW_SEED_DATA")

    @staticmethod
    def snapshot() -> dict[str, float]:
        src = ROOT / "apps" / "api" / "src"
        return {str(p): p.stat().st_mtime for p in src.rglob("*") if p.is_file()}


if __name__ == "__main__":
    unittest.main()
