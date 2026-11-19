"""Errors raised when a package registry cannot be read. Every message is fixed text that is safe to show.

None of them carries the registry's own words, a URL with a query string, or an exception's text.
"""

from __future__ import annotations


class RegistryError(Exception):
    """A registry could not be read, or answered with something VYRON will not accept."""


class PackageNotFoundError(RegistryError):
    """The registry answered 404 for this package name."""


class InvalidPackageNameError(RegistryError, ValueError):
    """The name is not one the registry could have issued, so no request was made."""


class RegistryRateLimitedError(RegistryError):
    """The registry answered 429. It sends no reliable wait time, so none is carried."""
