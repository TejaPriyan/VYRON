"""Vercel Python WSGI entrypoint for previewing the VYRON prototype and seed-backed API.

DEVELOPMENT / PREVIEW ONLY. Serves the browser prototype pages, SEO/AEO/GEO discovery files, compiled
ES modules, and the real standard-library ``vyron_api`` application (seed graph + live GitHub and
PyPI/npm readers). Nothing is stored in a database, and in-memory caches and rate limits are per
serverless instance. UNVERIFIED on Vercel's live cloud runtime until deployed there.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API_SRC = ROOT / "apps" / "api" / "src"
PROTOTYPE = ROOT / "apps" / "web" / "prototype"

if str(API_SRC) not in sys.path:
    sys.path.insert(0, str(API_SRC))

from vyron_api.devserver import configure  # noqa: E402


def _load_serve_module() -> Any:
    spec = importlib.util.spec_from_file_location("vyron_vercel_serve", PROTOTYPE / "serve.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load apps/web/prototype/serve.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def build_vercel_app() -> Any:
    serve = _load_serve_module()
    enable_github = os.environ.get("VYRON_ENABLE_GITHUB", "true").strip().lower() != "false"
    enable_registries = os.environ.get("VYRON_ENABLE_REGISTRIES", "true").strip().lower() != "false"
    allow_token = os.environ.get("VYRON_ALLOW_TOKEN_ON_NETWORK", "true").strip().lower() != "false"
    application, _status = configure(
        "0.0.0.0",  # noqa: S104 - virtual bind host for cloud reverse-proxy Host headers
        github=enable_github,
        registries=enable_registries,
        allow_token_on_network=allow_token,
        rate_limit=True,
    )
    return serve.make_app(application=application, trusted_proxy_hops=1)


app = build_vercel_app()
