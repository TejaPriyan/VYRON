#!/usr/bin/env python3
"""Compile the browser prototype with the TypeScript compiler alone (no node_modules, no bundler).

    python3 scripts/build_prototype.py          # emit apps/web/prototype/dist/js
    python3 scripts/build_prototype.py --check  # type-check the prototype only; write nothing

Needs ``tsc`` on PATH (or pass ``--tsc``). The output is plain ES modules that the browser loads
directly, mirroring the repository tree, so no import map or bundler is involved. ``dist/`` is ignored
by git: it is rebuilt from source.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "apps" / "web" / "prototype" / "tsconfig.json"
DIST = ROOT / "apps" / "web" / "prototype" / "dist"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="type-check without writing output")
    parser.add_argument("--tsc", default=None, help="path to the tsc executable")
    args = parser.parse_args(argv)
    tsc = args.tsc or shutil.which("tsc")
    if not tsc:
        print("tsc was not found on PATH. Install TypeScript or pass --tsc /path/to/tsc.", file=sys.stderr)
        return 2
    command = [tsc, "-p", str(CONFIG)]
    if args.check:
        command.append("--noEmit")
    else:
        # A stale file from a deleted module must never be served, so start from nothing.
        shutil.rmtree(DIST, ignore_errors=True)
    result = subprocess.run(command, cwd=ROOT, check=False)  # noqa: S603
    if result.returncode != 0:
        return result.returncode
    if not args.check:
        count = sum(1 for _ in (DIST / "js").rglob("*.js"))
        print(f"Emitted {count} modules to {DIST.relative_to(ROOT)}/js")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
