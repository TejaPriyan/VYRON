"""Make the repository's standard-library tooling importable, whatever directory tests run from.

Imported first by every file in tests/ (``import _paths``), so ``python3 -m unittest discover -s tests``
works with no PYTHONPATH. The API package is added too, so these tests can check the domain enums. Its test
doubles and the scripts directory are importable as well, for the stand-in GitHub server.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for relative in (
    "apps/api/src",
    "apps/web/design",
    "apps/api/tests",
    "scripts",
    "packages/types/tools",
    "infrastructure/docker",
):
    path = str(ROOT / relative)
    if path not in sys.path:
        sys.path.insert(0, path)
