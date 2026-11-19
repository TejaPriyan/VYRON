"""A small static checker for Dockerfiles and .dockerignore files. Standard library only.

It checks structure and hardening rules the repository has decided on; it is NOT a Dockerfile
validator and cannot say whether an image builds. Rules are named so a test can assert exactly which
one fires. Findings are advice for a reviewer, produced by code that has never seen a daemon.
"""

from __future__ import annotations

import glob
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

INSTRUCTIONS = {
    "ADD", "ARG", "CMD", "COPY", "ENTRYPOINT", "ENV", "EXPOSE", "FROM", "HEALTHCHECK", "LABEL",
    "MAINTAINER", "ONBUILD", "RUN", "SHELL", "STOPSIGNAL", "USER", "VOLUME", "WORKDIR",
}  # fmt: skip
SECRET_NAME = re.compile(r"(secret|password|passwd|token|api_?key|private_?key|credential)", re.I)
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:[:-]+([^}]*))?\}|\$([A-Za-z_][A-Za-z0-9_]*)")


@dataclass(frozen=True, slots=True)
class Instruction:
    line: int
    name: str
    args: str


@dataclass(frozen=True, slots=True)
class Finding:
    line: int
    rule: str
    message: str


@dataclass(slots=True)
class Stage:
    index: int
    name: str | None
    image: str
    parent: Stage | None
    line: int
    instructions: list[Instruction] = field(default_factory=list)

    def chain(self) -> list[Instruction]:
        """This stage's instructions preceded by those it inherits through ``FROM <earlier stage>``."""
        return (self.parent.chain() if self.parent else []) + self.instructions


class DockerfileError(ValueError):
    pass


def parse(text: str) -> list[Instruction]:
    """Instructions with continuation lines joined. Comments are dropped; heredocs are rejected."""
    out: list[Instruction] = []
    buffer = ""
    start = 0
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if buffer == "" and (stripped == "" or stripped.startswith("#")):
            continue
        if buffer != "" and stripped.startswith("#"):
            continue  # a comment inside a continued instruction
        if buffer == "":
            start = number
        if raw.rstrip().endswith("\\"):
            buffer += raw.rstrip()[:-1] + " "
            continue
        buffer += raw
        joined, buffer = buffer.strip(), ""
        if "<<" in joined and re.search(r"<<-?\s*['\"]?\w+", joined):
            raise DockerfileError(f"line {start}: heredocs are not supported by this checker")
        name, _, args = joined.partition(" ")
        if name.upper() not in INSTRUCTIONS:
            raise DockerfileError(f"line {start}: unknown instruction {name!r}")
        out.append(Instruction(start, name.upper(), args.strip()))
    if buffer.strip():
        raise DockerfileError("file ends inside a continued instruction")
    return out


def _expand(value: str, variables: dict[str, str]) -> str | None:
    """Substitute ARG/ENV defaults; None when a variable has no known value."""
    missing = False

    def sub(match: re.Match[str]) -> str:
        nonlocal missing
        name = match.group(1) or match.group(3)
        if name in variables:
            return variables[name]
        if match.group(2) is not None:
            return match.group(2)
        missing = True
        return ""

    result = _VAR.sub(sub, value)
    return None if missing else result


def _key_values(args: str) -> list[tuple[str, str]]:
    """``KEY=value KEY2="v 2"`` or the legacy ``KEY value`` form."""
    if "=" not in args.split(" ", 1)[0]:
        key, _, value = args.partition(" ")
        return [(key, value.strip())]
    pairs: list[tuple[str, str]] = []
    for match in re.finditer(r'([A-Za-z_][A-Za-z0-9_]*)=("(?:[^"\\]|\\.)*"|\'[^\']*\'|\S*)', args):
        value = match.group(2)
        if value[:1] in "\"'" and value[-1:] == value[:1]:
            value = value[1:-1]
        pairs.append((match.group(1), value))
    return pairs


def stages_of(instructions: list[Instruction]) -> tuple[dict[str, str], list[Stage]]:
    """Global ARG defaults and the stages, resolved through ARG-based image names."""
    global_args: dict[str, str] = {}
    stages: list[Stage] = []
    for ins in instructions:
        if ins.name == "ARG" and not stages:
            for key, value in _key_values(ins.args) if "=" in ins.args else [(ins.args.strip(), "")]:
                global_args[key] = value
        elif ins.name == "FROM":
            parts = [p for p in ins.args.split() if not p.startswith("--")]
            alias = parts[2] if len(parts) >= 3 and parts[1].upper() == "AS" else None
            if len(parts) not in (1, 3) or (len(parts) == 3 and parts[1].upper() != "AS"):
                raise DockerfileError(f"line {ins.line}: malformed FROM")
            image = _expand(parts[0], global_args)
            parent = next((s for s in stages if s.name and s.name == parts[0]), None)
            stages.append(Stage(len(stages), alias, image if image is not None else parts[0], parent, ins.line))
        elif stages:
            stages[-1].instructions.append(ins)
        else:
            raise DockerfileError(f"line {ins.line}: {ins.name} before the first FROM (only ARG is allowed)")
    return global_args, stages


def _image_problem(ref: str) -> str | None:
    if "$" in ref:
        return f"image reference {ref!r} cannot be resolved from ARG defaults"
    if "@sha256:" in ref:
        return None
    name, _, tag = ref.rpartition(":") if ":" in ref.split("/")[-1] else (ref, "", "")
    if not tag:
        return f"image {ref!r} has no tag (an untagged image means :latest)"
    if tag == "latest":
        return f"image {ref!r} uses :latest"
    return None


def _split_sources(args: str) -> tuple[list[str], list[str]]:
    """(flags, paths) for COPY/ADD; handles the JSON-array form."""
    if args.startswith("["):
        return [], [str(p) for p in json.loads(args)]
    parts = args.split()
    flags = [p for p in parts if p.startswith("--")]
    return flags, [p for p in parts if not p.startswith("--")]


def lint(text: str, *, context: Path, pending_sources: frozenset[str] = frozenset()) -> list[Finding]:
    """Findings for one Dockerfile. ``context`` is the build context directory (the repository root)."""
    findings: list[Finding] = []
    add = lambda line, rule, message: findings.append(Finding(line, rule, message))  # noqa: E731
    instructions = parse(text)
    global_args, stages = stages_of(instructions)
    if not stages:
        return [Finding(1, "no-from", "the file has no FROM")]
    stage_names = [s.name for s in stages if s.name]

    for key, value in global_args.items():
        if SECRET_NAME.search(key) and value:
            add(1, "secret-in-arg", f"ARG {key} has a default value; secrets must not be baked into an image")

    for stage in stages:
        if stage.parent is None:
            problem = _image_problem(stage.image)
            if problem:
                add(stage.line, "image-pin", problem)
        env = dict(global_args)
        for ins in stage.chain():
            if ins.name in ("ENV", "ARG"):
                pairs = _key_values(ins.args) if "=" in ins.args or ins.name == "ENV" else [(ins.args.strip(), "")]
                for key, value in pairs:
                    env[key] = value
                    if SECRET_NAME.search(key) and value and not value.startswith("$"):
                        add(ins.line, "secret-in-env", f"{ins.name} {key} has a literal value")
            if ins.name == "ADD" and re.match(r"^(--\S+\s+)*\[?\"?https?://", ins.args):
                add(ins.line, "remote-add", "ADD from a URL: download in a RUN step with a checksum instead")
            if ins.name == "RUN":
                if re.search(r"\b(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(ba|z)?sh\b", ins.args):
                    add(ins.line, "pipe-to-shell", "piping a download into a shell")
                if re.search(r"\bsudo\b", ins.args):
                    add(ins.line, "sudo", "sudo in a build step")
                if re.search(r"chmod\s+(-R\s+)?(0?777|a\+rwx)", ins.args):
                    add(ins.line, "chmod-777", "world-writable permissions")
                if re.search(r"apt(-get)?\s+install", ins.args) and "--no-install-recommends" not in ins.args:
                    add(ins.line, "apt-recommends", "apt-get install without --no-install-recommends")
                caches = "--no-cache-dir" in ins.args or env.get("PIP_NO_CACHE_DIR") == "1"
                if re.search(r"pip3?\s+install", ins.args) and not caches:
                    add(ins.line, "pip-cache", "pip install without --no-cache-dir or PIP_NO_CACHE_DIR=1")
            if ins.name == "WORKDIR" and not (ins.args.startswith("/") or ins.args.startswith("$")):
                add(ins.line, "relative-workdir", f"WORKDIR {ins.args!r} is not absolute")
            if ins.name == "EXPOSE":
                for port in ins.args.split():
                    if not re.fullmatch(r"\d{1,5}(/(tcp|udp))?", port):
                        add(ins.line, "expose", f"EXPOSE {port!r} is not a port number")
            if ins.name in ("CMD", "ENTRYPOINT"):
                try:
                    ok = isinstance(json.loads(ins.args), list)
                except json.JSONDecodeError:
                    ok = False
                if not ok:
                    add(
                        ins.line, "shell-form",
                        f"{ins.name} must use the exec (JSON array) form so signals reach the process",
                    )
            if ins.name in ("COPY", "ADD"):
                flags, paths = _split_sources(ins.args)
                from_flag = next((f for f in flags if f.startswith("--from=")), None)
                if from_flag:
                    ref = from_flag.split("=", 1)[1]
                    earlier = [s.name for s in stages if s.index < stage.index and s.name]
                    if ref not in earlier and not ref.isdigit():
                        known = ", ".join(earlier) or "none"
                        add(ins.line, "copy-from", f"--from={ref} is not an earlier stage ({known})")
                    continue
                for source in paths[:-1]:
                    if "$" in source or source.startswith(("http://", "https://")):
                        continue
                    if source in pending_sources:
                        continue
                    if not glob.glob(str(context / source)) and not (context / source).exists():
                        add(
                            ins.line, "copy-source-missing",
                            f"COPY source {source!r} does not exist in the build context",
                        )

        runs = any(i.name in ("CMD", "ENTRYPOINT") for i in stage.instructions)
        if runs:
            users = [i for i in stage.chain() if i.name == "USER"]
            if not users:
                add(stage.line, "no-user", f"stage {stage.name or stage.index} runs as root (no USER)")
            else:
                user = users[-1].args.split(":")[0]
                if user in ("root", "0") or not (user.isdigit() or re.fullmatch(r"[a-z_][a-z0-9_-]*", user)):
                    add(users[-1].line, "root-user", f"final USER is {users[-1].args!r}")
                elif not user.isdigit():
                    add(
                        users[-1].line, "named-user",
                        f"USER {user!r}: use a numeric uid so orchestrators can verify non-root",
                    )
            health = [i for i in stage.chain() if i.name == "HEALTHCHECK"]
            if not health or health[-1].args.strip().upper().startswith("NONE"):
                add(stage.line, "no-healthcheck", f"stage {stage.name or stage.index} has no HEALTHCHECK")

    if len(set(stage_names)) != len(stage_names):
        add(1, "duplicate-stage", "two stages share a name")
    return sorted(findings, key=lambda f: (f.line, f.rule))


# --- .dockerignore --------------------------------------------------------------------------------


def _pattern_to_regex(pattern: str) -> re.Pattern[str]:
    out: list[str] = []
    parts = pattern.strip("/").split("/")
    for i, part in enumerate(parts):
        if part == "**":
            out.append("(?:.+/)?" if i < len(parts) - 1 else ".*")
            continue
        piece = "".join(
            "[^/]*" if ch == "*" else "[^/]" if ch == "?" else re.escape(ch) for ch in part
        )
        out.append(piece + ("/" if i < len(parts) - 1 else ""))
    return re.compile("^" + "".join(out) + "$")


def parse_dockerignore(text: str) -> list[tuple[bool, re.Pattern[str]]]:
    """(negated, regex) per pattern, in file order."""
    rules: list[tuple[bool, re.Pattern[str]]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        rules.append((negated, _pattern_to_regex(line[1:] if negated else line)))
    return rules


def is_ignored(path: str, rules: list[tuple[bool, re.Pattern[str]]]) -> bool:
    """Whether a context-relative file path is excluded. A pattern that matches a parent directory
    excludes everything under it; the last matching pattern wins. (Docker's own matcher has extra
    cases for re-including files under an excluded directory; none are used here.)"""
    segments = path.split("/")
    prefixes = ["/".join(segments[: i + 1]) for i in range(len(segments))]
    ignored = False
    for negated, regex in rules:
        if any(regex.match(prefix) for prefix in prefixes):
            ignored = not negated
    return ignored
