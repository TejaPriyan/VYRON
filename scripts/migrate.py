#!/usr/bin/env python3
"""Run database migration and seed loading against SQLite.

Standard library only. Sets up local relational persistence, loads development seed SQL,
and verifies relational integrity constraints.

Usage:
    python scripts/migrate.py --db vyron.db --init
    python scripts/migrate.py --db vyron.db --seed
    python scripts/migrate.py --db vyron.db --verify
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))

from vyron_api.db.seedsql import render_seed_sql  # noqa: E402
from vyron_api.db.sqlite import SqliteDatabase  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage VYRON database schema and seed data.")
    parser.add_argument(
        "--db",
        default="vyron.db",
        help="Path to the SQLite database file (default: vyron.db)",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Initialize database tables and views",
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Load development seed SQL into the database",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify database integrity views and report counts",
    )
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    db_path = Path(args.db).resolve()
    db = SqliteDatabase(db_path)
    try:
        if args.init:
            print(f"[*] Initializing database schema at {db_path.relative_to(ROOT)}...")
            db.init_schema()
            print("[+] Schema initialized successfully.")

        if args.seed:
            print("[*] Loading development seed SQL...")
            db.init_schema()
            seed_sql = render_seed_sql()
            db.load_seed_sql(seed_sql)
            print("[+] Development seed loaded successfully.")

        if args.verify or (not args.init and not args.seed):
            print(f"\n--- Database Summary: {db_path.name} ---")
            counts = db.counts()
            for table, count in sorted(counts.items()):
                print(f"  {table:24}: {count}")

            violations = db.verify_integrity()
            if violations:
                print(f"\n[!] Integrity check FAILED ({len(violations)} violations):")
                for v in violations:
                    print(f"    - {v}")
                return 1
            print("\n[+] Integrity check PASSED: 0 violations found.")
    finally:
        db.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
