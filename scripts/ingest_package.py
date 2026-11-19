#!/usr/bin/env python3
"""Read one package's declared dependencies from PyPI or npm and show how VYRON would record them.

Standard library only. This reads a public registry (one request) and builds the entities, evidence and
DEPENDS_ON relationships in memory. NOTHING IS STORED: the graph exists for this one command and is discarded
when it ends. There is no database write path yet.

    python scripts/ingest_package.py --pypi flask
    python scripts/ingest_package.py --npm express --kind framework

Only dependencies with no condition are recorded. A PyPI requirement with a marker (an extra, a Python version,
a platform) is counted and left out, and the evidence claim says so.
Exit status: 0 read and built, 1 the registry could not be read or has no such package, 2 bad arguments.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))

from vyron_api.adapters.memory_graph import InMemoryGraph  # noqa: E402
from vyron_api.domain.entities import EntityKind  # noqa: E402
from vyron_api.registries import (  # noqa: E402
    InvalidPackageNameError,
    RegistryError,
    RegistryIngestService,
    fetch_npm_package,
    fetch_pypi_package,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read a package's declared dependencies from PyPI or npm (nothing is stored)."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pypi", metavar="PACKAGE", help="PyPI package name")
    group.add_argument("--npm", metavar="PACKAGE", help="npm package name (scoped names such as @scope/name work)")
    parser.add_argument(
        "--kind",
        choices=["library", "framework", "technology"],
        default="library",
        help="entity kind for the package itself (default: library)",
    )
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    kind = EntityKind(args.kind)
    graph = InMemoryGraph()
    service = RegistryIngestService(graph)

    try:
        if args.pypi:
            print(f"[*] Reading PyPI package {args.pypi!r}...")
            pkg = fetch_pypi_package(args.pypi)
            result = service.ingest_pypi(args.pypi, kind=kind, pkg=pkg)
            registry_name = "PyPI"
            skipped = len(pkg.conditional_dependencies)
        else:
            print(f"[*] Reading npm package {args.npm!r}...")
            npm_pkg = fetch_npm_package(args.npm)
            result = service.ingest_npm(args.npm, kind=kind, pkg=npm_pkg)
            registry_name = "npm"
            skipped = 0
    except InvalidPackageNameError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except RegistryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("\n--- Package ---")
    print(f"Name:         {result.main_entity.name} ({result.main_entity.slug})")
    print(f"Kind:         {result.main_entity.kind.value}")
    print(f"Provenance:   {result.main_entity.provenance.value}")
    print(f"Registry:     {registry_name}")
    print(f"Source URL:   {result.source.url}")
    print(f"Claim:        {result.evidence.claim}")

    print(f"\n--- Dependencies recorded ({len(result.dependencies)}) ---")
    for dep in result.dependencies:
        print(f"  - {dep.name}")
    if skipped:
        print(f"  ({skipped} more apply only under a condition and were not recorded)")

    print("\n--- Built in memory (not stored) ---")
    print(f"Entities:      {graph.entity_count}")
    print(f"Relationships: {graph.relationship_count}")
    print(f"Evidence rows: {len(service.evidence)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
