#!/usr/bin/env python3
"""Regenerate docs/openapi.json from the route registry.

Usage:  python3 scripts/generate_openapi.py          # write the file
        python3 scripts/generate_openapi.py --check  # exit 1 if the committed file is stale
Standard library only.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))

from vyron_api.api.openapi import render_openapi_json  # noqa: E402

TARGET = ROOT / "docs" / "openapi.json"


def main(argv: list[str]) -> int:
    rendered = render_openapi_json()
    if "--check" in argv:
        if not TARGET.exists() or TARGET.read_text(encoding="utf-8") != rendered:
            print(f"{TARGET.relative_to(ROOT)} is stale; run scripts/generate_openapi.py")
            return 1
        print(f"{TARGET.relative_to(ROOT)} is up to date")
        return 0
    TARGET.write_text(rendered, encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)} ({len(rendered.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
