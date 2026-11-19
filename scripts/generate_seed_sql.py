#!/usr/bin/env python3
"""Regenerate infrastructure/database/seed/dev_seed.sql from the Python development seed.

Usage:  python3 scripts/generate_seed_sql.py          # write the file
        python3 scripts/generate_seed_sql.py --check  # exit 1 if the committed file is stale
Standard library only.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))

from vyron_api.db.seedsql import render_seed_sql  # noqa: E402

TARGET = ROOT / "infrastructure" / "database" / "seed" / "dev_seed.sql"


def main(argv: list[str]) -> int:
    rendered = render_seed_sql()
    if "--check" in argv:
        if not TARGET.exists() or TARGET.read_text(encoding="utf-8") != rendered:
            print(f"{TARGET.relative_to(ROOT)} is stale; run scripts/generate_seed_sql.py")
            return 1
        print(f"{TARGET.relative_to(ROOT)} is up to date")
        return 0
    TARGET.write_text(rendered, encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)} ({len(rendered.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
