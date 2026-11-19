"""Run the development API:  ``PYTHONPATH=src python3 -m vyron_api.devserver``.

Standard library only. DEVELOPMENT ONLY: single-process, in-memory rate limiting and cache, and ``wsgiref`` as the
server, which handles one request at a time. The graph is the clearly-labelled seed. The two GitHub routes are the
exception: unless ``--no-github`` is given they read the real GitHub REST API at request time. Set ``GITHUB_TOKEN``
(a token with no scopes is enough for public repositories) to raise GitHub's limit from 60 to 5,000 requests an
hour. The token stays in this process; it is sent only to api.github.com and never appears in a response or log.
"""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Mapping

from vyron_api.api.app import Application
from vyron_api.api.bootstrap import build_seed_application
from vyron_api.api.hostcheck import LOOPBACK_HOSTS, is_loopback_bind
from vyron_api.api.wsgi import make_dev_server
from vyron_api.github.provider import build_live_provider
from vyron_api.registries.provider import build_live_package_provider

BANNER = "VYRON development server: seed graph, in-memory limits, not for production use."


class StartupError(ValueError):
    """The command line and environment do not describe a server that is safe to start."""


def configure(
    host: str,
    *,
    github: bool = True,
    registries: bool = True,
    allow_token_on_network: bool = False,
    rate_limit: bool = True,
    environ: Mapping[str, str] | None = None,
) -> tuple[Application, str]:
    """The application to serve and the lines saying where its GitHub and package-registry data come from.

    Nothing is requested from GitHub, PyPI or npm here; a provider only makes calls when a route is used. The
    registries need no credentials.
    """
    environ = os.environ if environ is None else environ
    loopback = is_loopback_bind(host)
    token = (environ.get("GITHUB_TOKEN") or "").strip() or None
    hosts = LOOPBACK_HOSTS if loopback else None
    packages = build_live_package_provider() if registries else None
    registry_line = (
        "Registries: live (pypi.org and registry.npmjs.org, no credentials)"
        if registries
        else "Registries: off (--no-registries); the package route answers 503"
    )
    if not github:
        return (
            build_seed_application(packages=packages, allowed_hosts=hosts, rate_limit=rate_limit),
            "GitHub: off (--no-github); the GitHub routes answer 503\n" + registry_line,
        )
    if token and not loopback and not allow_token_on_network:
        raise StartupError(
            "refusing to use GITHUB_TOKEN while listening on a non-loopback address, because anyone who can "
            "reach this port could spend its quota; bind 127.0.0.1, unset GITHUB_TOKEN, pass --no-github, "
            "or pass --allow-token-on-network if the network is yours (a container's published port, say)"
        )
    try:
        provider = build_live_provider(token=token)
    except ValueError as exc:  # the message never contains the token
        raise StartupError(f"GITHUB_TOKEN is not usable: {exc}") from exc
    status = (
        "GitHub: live, authenticated with GITHUB_TOKEN (5,000 requests/hour)"
        if token
        else "GitHub: live, unauthenticated (60 requests/hour; set GITHUB_TOKEN for more)"
    )
    if token and not loopback:
        status += "\nWARNING: this address is not loopback; anyone who can reach it can spend the token's quota"
    application = build_seed_application(github=provider, packages=packages, allowed_hosts=hosts, rate_limit=rate_limit)
    return application, status + "\n" + registry_line


def main(argv: list[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=BANNER)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--no-github", action="store_true", help="do not call GitHub; the two GitHub routes answer 503"
    )
    parser.add_argument(
        "--no-registries", action="store_true", help="do not call PyPI or npm; the package route answers 503"
    )
    parser.add_argument(
        "--allow-token-on-network",
        action="store_true",
        help="use GITHUB_TOKEN even though the address is not loopback (otherwise refused)",
    )
    args = parser.parse_args(argv)
    try:
        application, github_line = configure(
            args.host,
            github=not args.no_github,
            registries=not args.no_registries,
            allow_token_on_network=args.allow_token_on_network,
            environ=environ,
        )
    except StartupError as exc:
        parser.error(str(exc))
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    server = make_dev_server(application, args.host, args.port)
    host_line = "any Host header" if not is_loopback_bind(args.host) else "loopback Host headers only"
    print(f"{BANNER}\n{github_line}\nAnswers to: {host_line}")
    print(f"Listening on http://{args.host}:{server.server_port}/api/v1/meta")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
