"""Package registry ingestion drivers for PyPI and npm.

Standard library only. A package's own declared dependencies become ``sourced`` entities and DEPENDS_ON
relationships, each with a recorded Source and Evidence. Nothing is stored beyond the graph the caller passes in.
The drivers have been run against the real registries only by the project owner, not by any automated test.
"""

from vyron_api.registries.common import slugify
from vyron_api.registries.errors import InvalidPackageNameError, PackageNotFoundError, RegistryError
from vyron_api.registries.npm import NpmPackage, fetch_npm_package, npm_package_to_graph, validate_npm_name
from vyron_api.registries.pypi import (
    PyPIPackage,
    fetch_pypi_package,
    normalize_pypi_name,
    pypi_package_to_graph,
)
from vyron_api.registries.service import IngestResult, RegistryIngestService

__all__ = [
    "IngestResult",
    "InvalidPackageNameError",
    "NpmPackage",
    "PackageNotFoundError",
    "PyPIPackage",
    "RegistryError",
    "RegistryIngestService",
    "fetch_npm_package",
    "fetch_pypi_package",
    "normalize_pypi_name",
    "npm_package_to_graph",
    "pypi_package_to_graph",
    "slugify",
    "validate_npm_name",
]
