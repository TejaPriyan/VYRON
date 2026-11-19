#!/usr/bin/env python3
"""Refresh the generated route reference inside docs/API.md.

Usage:  python3 scripts/generate_api_docs.py          # rewrite the generated section
        python3 scripts/generate_api_docs.py --check  # exit 1 if it is stale
Standard library only. The hand-written parts of docs/API.md are never touched.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))

from vyron_api.api.apidocs import render_route_reference, splice  # noqa: E402

TARGET = ROOT / "docs" / "API.md"


def main(argv: list[str]) -> int:
    current = TARGET.read_text(encoding="utf-8")
    updated = splice(current, render_route_reference())
    if "--check" in argv:
        if updated != current:
            print(f"{TARGET.relative_to(ROOT)} route reference is stale; run scripts/generate_api_docs.py")
            return 1
        print(f"{TARGET.relative_to(ROOT)} route reference is up to date")
        return 0
    TARGET.write_text(updated, encoding="utf-8")
    print(f"updated {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
