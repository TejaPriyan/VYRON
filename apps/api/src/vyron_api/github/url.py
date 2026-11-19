"""Strict, dependency-free parsing of user-supplied GitHub references.

User input is untrusted. This module never fetches anything: it only turns a string into an owner,
an ``owner/repo`` pair or a classified GitHub target, or raises. Only github.com is accepted, so a
parsed reference can never point the server at another host (SSRF), and anything unusual is
rejected rather than repaired.

Accepted forms: ``owner/repo``, ``github.com/owner/repo``, http(s) URLs on github.com or
www.github.com, ``git@github.com:owner/repo.git`` and ``ssh://git@github.com/owner/repo.git``.
Rejected: other hosts, credentials (except the literal ``git`` user on ssh), explicit ports,
percent-encoding, backslashes, non-ASCII, control characters and over-long input.

Heuristic limits, on purpose: owner names follow GitHub's documented username rules (ASCII
alphanumerics and single hyphens, no leading/trailing hyphen, max 39 chars), so logins that
contain underscores are not accepted. Names are validated by shape only; whether the owner or
repository exists is for the GitHub API to say.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit

_MAX_INPUT_LENGTH = 300
_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_PRINTABLE_ASCII_RE = re.compile(r"^[\x21-\x7e]+$")
_SCP_RE = re.compile(r"^git@github\.com:(?P<path>\S+)$", re.IGNORECASE)
_NUMBER_RE = re.compile(r"^[0-9]{1,10}$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
_REF_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._+@-]{1,100}$")
_ALLOWED_HOSTS = frozenset({"github.com", "www.github.com"})
_ALLOWED_SCHEMES = frozenset({"http", "https", "ssh"})
# First path segments that are site routes, not accounts (heuristic guard against mis-parsing).
_RESERVED_OWNERS = frozenset(
    {
        "orgs",
        "settings",
        "topics",
        "search",
        "marketplace",
        "sponsors",
        "apps",
        "features",
        "pricing",
        "login",
        "notifications",
        "explore",
        "new",
    }
)


class InvalidRepoReferenceError(ValueError):
    """The input is not a safe, well-formed GitHub reference."""


class TargetKind(StrEnum):
    OWNER = "owner"
    REPOSITORY = "repository"
    ISSUE = "issue"
    PULL_REQUEST = "pull_request"
    COMMIT = "commit"
    RELEASE = "release"
    TREE = "tree"
    BLOB = "blob"


@dataclass(frozen=True, slots=True)
class OwnerRef:
    owner: str

    @property
    def key(self) -> str:
        return self.owner.lower()

    @property
    def html_url(self) -> str:
        return f"https://github.com/{self.owner}"

    @property
    def api_path(self) -> str:
        return f"/users/{self.owner}"


@dataclass(frozen=True, slots=True)
class RepoRef:
    owner: str
    repo: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def key(self) -> str:
        """Case-insensitive identity (GitHub treats owner/repo names case-insensitively)."""
        return self.full_name.lower()

    @property
    def html_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"

    @property
    def api_path(self) -> str:
        return f"/repos/{self.owner}/{self.repo}"


@dataclass(frozen=True, slots=True)
class GitHubTarget:
    """What a pasted link points at. ``repo`` is None only for owner links."""

    kind: TargetKind
    owner: str
    repo: str | None = None
    number: int | None = None  # issue / pull request number
    ref: str | None = None  # commit sha, release tag, or the raw tree/blob remainder

    @property
    def repo_ref(self) -> RepoRef | None:
        return RepoRef(self.owner, self.repo) if self.repo else None


def _split_segments(raw: str, *, allow_bare_owner: bool) -> tuple[list[str], bool]:
    """Validate ``raw`` as a GitHub location and return (path segments, was_bare_shorthand)."""
    text = raw.strip()
    if not text:
        raise InvalidRepoReferenceError("reference is empty")
    if len(text) > _MAX_INPUT_LENGTH:
        raise InvalidRepoReferenceError("reference is too long")
    if not _PRINTABLE_ASCII_RE.fullmatch(text):
        raise InvalidRepoReferenceError("reference contains unsupported characters")
    if "\\" in text or "%" in text:
        raise InvalidRepoReferenceError("reference contains unsupported characters")

    scp = _SCP_RE.match(text)
    lowered = text.lower()
    shorthand = False
    if scp:
        path = scp.group("path")
        shorthand = True  # "owner/repo[.git]" only, same strictness as bare shorthand
    elif "://" in text:
        path = _checked_url_path(text)
    elif lowered.startswith(("github.com/", "www.github.com/")):
        path = _checked_url_path(f"https://{text}")
    else:
        path = text
        shorthand = True

    segments = path.split("/")
    if not shorthand and segments and segments[0] == "":
        segments = segments[1:]  # URL paths start with "/"; bare "owner/repo" input must not
    while segments and segments[-1] == "":
        segments.pop()
    if not segments or not segments[0]:
        raise InvalidRepoReferenceError("expected at least an owner")
    if shorthand and len(segments) > 2:
        raise InvalidRepoReferenceError("expected exactly 'owner/repo'")
    if shorthand and len(segments) == 1 and not allow_bare_owner:
        raise InvalidRepoReferenceError("expected an owner and a repository name")
    return segments, shorthand


def _checked_url_path(url: str) -> str:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise InvalidRepoReferenceError("URL is malformed") from exc
    if parts.scheme not in _ALLOWED_SCHEMES:
        raise InvalidRepoReferenceError("only http(s) and ssh GitHub URLs are accepted")
    ssh_git_user = parts.scheme == "ssh" and parts.username == "git" and parts.password is None
    if (parts.username is not None or parts.password is not None) and not ssh_git_user:
        raise InvalidRepoReferenceError("credentials in URLs are not accepted")
    if parts.scheme == "ssh" and not ssh_git_user:
        raise InvalidRepoReferenceError("ssh URLs must use the 'git' user")
    if port is not None:
        raise InvalidRepoReferenceError("explicit ports are not accepted")
    if parts.hostname not in _ALLOWED_HOSTS:
        raise InvalidRepoReferenceError("only github.com is supported")
    return parts.path


def _checked_owner(owner: str) -> str:
    if not _OWNER_RE.fullmatch(owner) or owner.lower() in _RESERVED_OWNERS:
        raise InvalidRepoReferenceError(f"invalid owner name: {owner!r}")
    return owner


def _checked_repo(repo: str) -> str:
    if repo.lower().endswith(".git"):
        repo = repo[:-4]
    if repo in {"", ".", ".."} or not _REPO_RE.fullmatch(repo):
        raise InvalidRepoReferenceError(f"invalid repository name: {repo!r}")
    return repo


def parse_repo_reference(raw: str) -> RepoRef:
    """Parse ``owner/repo`` or any accepted link into a ``RepoRef`` (extra path is ignored)."""
    segments, _ = _split_segments(raw, allow_bare_owner=False)
    if len(segments) < 2 or not segments[1]:
        raise InvalidRepoReferenceError("expected an owner and a repository name")
    return RepoRef(_checked_owner(segments[0]), _checked_repo(segments[1]))


def is_account_name(name: str) -> bool:
    """True when ``name`` satisfies the rules this module applies to owners (so ``github.com/<name>`` is a profile)."""
    return bool(_OWNER_RE.fullmatch(name)) and name.lower() not in _RESERVED_OWNERS


def repo_ref_from_parts(owner: str, repo: str) -> RepoRef:
    """Validate an owner and a repository name that arrive as two separate values (route path segments).

    Unlike :func:`parse_repo_reference` nothing is re-parsed as a link, so ``github.com`` cannot be read as a
    host: each value must be a valid name on its own, or this raises ``InvalidRepoReferenceError``.
    """
    return RepoRef(_checked_owner(owner), _checked_repo(repo))


def parse_owner_reference(raw: str) -> OwnerRef:
    """Parse ``owner``, ``github.com/owner`` or a profile/organization link."""
    segments, _ = _split_segments(raw, allow_bare_owner=True)
    return OwnerRef(_checked_owner(segments[0]))


def parse_github_target(raw: str) -> GitHubTarget:
    """Classify a pasted link (owner, repository, issue, PR, commit, release, tree, blob)."""
    segments, shorthand = _split_segments(raw, allow_bare_owner=True)
    owner = _checked_owner(segments[0])
    if len(segments) == 1:
        return GitHubTarget(TargetKind.OWNER, owner)
    repo = _checked_repo(segments[1])
    rest = segments[2:]
    if shorthand or not rest:
        return GitHubTarget(TargetKind.REPOSITORY, owner, repo)

    section, tail = rest[0], rest[1:]
    if section in {"issues", "pull"} and tail:
        if not _NUMBER_RE.fullmatch(tail[0]):
            raise InvalidRepoReferenceError("issue and pull request links need a numeric id")
        kind = TargetKind.ISSUE if section == "issues" else TargetKind.PULL_REQUEST
        return GitHubTarget(kind, owner, repo, number=int(tail[0]))
    if section == "commit" and tail:
        if not _SHA_RE.fullmatch(tail[0]):
            raise InvalidRepoReferenceError("commit links need a 7-40 character hex sha")
        return GitHubTarget(TargetKind.COMMIT, owner, repo, ref=tail[0].lower())
    if section == "releases" and len(tail) >= 2 and tail[0] == "tag":
        tag = "/".join(tail[1:])
        if not all(_REF_SEGMENT_RE.fullmatch(part) for part in tail[1:]):
            raise InvalidRepoReferenceError("release tag contains unsupported characters")
        return GitHubTarget(TargetKind.RELEASE, owner, repo, ref=tag)
    if section in {"tree", "blob"} and tail:
        if not all(_REF_SEGMENT_RE.fullmatch(part) for part in tail):
            raise InvalidRepoReferenceError("path contains unsupported characters")
        kind = TargetKind.TREE if section == "tree" else TargetKind.BLOB
        # A branch name may contain "/", so the ref/path split is ambiguous; keep it raw.
        return GitHubTarget(kind, owner, repo, ref="/".join(tail))
    return GitHubTarget(TargetKind.REPOSITORY, owner, repo)
