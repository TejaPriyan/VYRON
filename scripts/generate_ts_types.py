#!/usr/bin/env python3
"""Generate packages/types/src/api.ts from docs/openapi.json.

Usage:  python3 scripts/generate_ts_types.py          # write the file
        python3 scripts/generate_ts_types.py --check  # exit 1 if it is stale
Standard library only. Regenerate docs/openapi.json first (scripts/generate_openapi.py).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "types" / "tools"))

from tsgen import load_spec, render_typescript  # noqa: E402

SPEC = ROOT / "docs" / "openapi.json"
TARGET = ROOT / "packages" / "types" / "src" / "api.ts"


def main(argv: list[str]) -> int:
    text = render_typescript(load_spec(SPEC))
    rel = TARGET.relative_to(ROOT)
    if "--check" in argv:
        if not TARGET.exists() or TARGET.read_text(encoding="utf-8") != text:
            print(f"{rel} is stale; run scripts/generate_ts_types.py")
            return 1
        print(f"{rel} is up to date")
        return 0
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(text, encoding="utf-8")
    print(f"wrote {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
