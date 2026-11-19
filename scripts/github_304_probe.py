"""Does a conditional request that GitHub answers 304 count against the rate limit? Ask GitHub and print what it said.

    python scripts/github_304_probe.py                       # about 2 of GitHub's 60 requests an hour (3 at most)
    GITHUB_TOKEN=... python scripts/github_304_probe.py      # the same with a token, to compare

WHY. GitHub's documentation says a 304 does not count against the primary limit. A reported run of the VYRON
repository page against the real GitHub showed the quota falling by 2 on a read that came back "Confirmed"
(two 304s), which contradicts that for unauthenticated use. This script settles it with the least machinery: plain
``urllib``, no VYRON code, so nothing of mine sits between you and GitHub's answer.

It reads the quota (free), reads one repository (counted), reads the quota, repeats the read with ``If-None-Match``
(a 304 if all is well), reads the quota again, and prints the used count before and after each step.

Run it on its own: anything else using this machine's GitHub quota while it runs (another script, another tool)
makes the numbers wrong, and it says so if the counts disagree with each other. A token, if set, is sent only to
api.github.com and is never printed. Exit status 0 means it got an answer; it does not mean a 304 was free.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any

BASE = "https://api.github.com"
DEFAULT_REPO = "pallets/flask"


def get(
    base: str, path: str, token: str | None, headers: Mapping[str, str] | None = None
) -> tuple[int, dict[str, str], bytes]:
    sent = {"Accept": "application/vnd.github+json", "User-Agent": "vyron-304-probe", **(headers or {})}
    request = urllib.request.Request(base + path, headers=sent)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            return response.status, {k.lower(): v for k, v in response.headers.items()}, response.read()
    except urllib.error.HTTPError as error:
        with error:
            return error.code, {k.lower(): v for k, v in error.headers.items()}, error.read()


def used(base: str, token: str | None) -> tuple[int | None, int | None]:
    """(used, remaining) of the core limit as GitHub reports it right now; asking is free."""
    status, _, body = get(base, "/rate_limit", token)
    if status != 200:
        return None, None
    core = json.loads(body).get("resources", {}).get("core", {})
    return core.get("used"), core.get("remaining")


def judge(deltas: Sequence[int | None]) -> str:
    if not deltas:
        return "could not tell: no 304 was received"
    known = [d for d in deltas if d is not None]
    if len(known) != len(deltas):
        return "could not tell: GitHub did not report a count at every step"
    if any(d < 0 for d in known):
        return "could not tell: the used count went down, so the quota window rolled over; run it again"
    if all(d == 0 for d in known):
        return "a 304 did NOT count against the limit"
    if all(d == 1 for d in known):
        return "a 304 DID count against the limit (1 each)"
    return f"mixed: the 304s cost {list(known)} requests each; something else may have used this quota meanwhile"


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=DEFAULT_REPO, metavar="OWNER/NAME", help="a public repository to read")
    parser.add_argument("--base-url", default=BASE, help=argparse.SUPPRESS)  # a loopback stand-in, to test the script
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ
    base = args.base_url.rstrip("/")
    repo_path = f"/repos/{args.repo}"
    real = base == BASE
    if not real and not base.startswith("http://127.0.0.1:"):
        print("error: --base-url is only for a local stand-in", file=sys.stderr)
        return 2
    token = (environ.get("GITHUB_TOKEN") or "").strip() or None if real else None
    print(f"target: {base}" + ("" if real else "  (STAND-IN, FIXTURE DATA: says nothing about GitHub)"))
    print("token:  " + ("set (value not shown)" if token else "not set"))

    try:
        before, remaining = used(base, token)
        print(f"\n1. quota before:               used={before} remaining={remaining}")
        status, headers, _ = get(base, repo_path, token)
        etag = headers.get("etag")
        remaining_header = headers.get("x-ratelimit-remaining")
        print(f"2. GET {repo_path}: HTTP {status}, etag={etag}, x-ratelimit-remaining={remaining_header}")
        if status != 200 or not etag:
            print("\nCould not get a 200 with an ETag, so there is nothing to revalidate. Nothing was learned.")
            return 2
        after_read, _ = used(base, token)
        print(f"3. quota after the 200:        used={after_read}")
        deltas: list[int | None] = []
        last = after_read
        for attempt in (1, 2):
            status, headers, _ = get(base, repo_path, token, {"If-None-Match": etag})
            now, _ = used(base, token)
            delta = None if now is None or last is None else now - last
            deltas.append(delta)
            print(f"{3 + attempt}. conditional GET #{attempt}: HTTP {status}, "
                  f"x-ratelimit-remaining={headers.get('x-ratelimit-remaining')}, used {last} -> {now} (cost {delta})")
            last = now
            if status != 304:
                print("   GitHub did not answer 304, so this step says nothing about 304s.")
                deltas.pop()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"\nCould not reach GitHub: {type(exc).__name__}. Nothing was learned.")
        return 2
    first_read_cost = None if before is None or after_read is None else after_read - before
    print(f"\nThe plain 200 cost {first_read_cost}; the conditional requests that came back 304 cost {deltas}.")
    print("RESULT: " + judge(deltas))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
