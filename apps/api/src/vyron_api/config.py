"""Environment-driven settings, validated once at startup (standard library only).

Secrets are read from the environment, never from code or files in the repository. They are wrapped
in ``Secret`` so they cannot leak through ``repr``, ``str``, f-strings, logs or tracebacks; the one
way to get the value is ``.reveal()``. Validation reports every problem at once and never echoes a
value back. Production is stricter than development: stable secrets are mandatory and seed data is
refused unless explicitly allowed.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

MIN_SECRET_LENGTH = 32
_LOG_LEVELS = ("debug", "info", "warning", "error", "critical")
_URL_SCHEMES: Mapping[str, tuple[str, ...]] = {
    "DATABASE_URL": ("postgresql://", "postgres://"),
    "NEO4J_URI": ("bolt://", "bolt+s://", "bolt+ssc://", "neo4j://", "neo4j+s://", "neo4j+ssc://"),
    "REDIS_URL": ("redis://", "rediss://"),
}
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,99}$")
_TRUE, _FALSE = {"1", "true", "yes"}, {"0", "false", "no", ""}


class ConfigError(ValueError):
    """The environment is not a valid VYRON configuration. The message never contains secrets."""


class Secret:
    """A value that refuses to be printed."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret('***')"

    __str__ = __repr__

    def __format__(self, spec: str) -> str:
        return "***"

    def __bool__(self) -> bool:
        return bool(self._value)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Secret) and hmac.compare_digest(
            self._value.encode(), other._value.encode()
        )  # defining __eq__ also makes Secret unhashable, so it cannot be a dict key or set member


class AppEnv(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class DataMode(StrEnum):
    SEED = "seed"
    DATABASE = "database"


def derive_secret(master: Secret, purpose: str) -> bytes:
    """A purpose-specific key from a master secret, so one secret never serves two jobs."""
    return hmac.new(master.reveal().encode("utf-8"), f"vyron:{purpose}:v1".encode(), hashlib.sha256).digest()


@dataclass(frozen=True, slots=True)
class Settings:
    app_env: AppEnv
    log_level: str
    data_mode: DataMode
    session_secret: Secret | None
    previous_session_secret: Secret | None
    trusted_proxy_hops: int
    database_url: Secret | None
    neo4j_uri: str | None
    neo4j_username: str | None
    neo4j_password: Secret | None
    redis_url: Secret | None
    github_token: Secret | None
    gemini_api_key: Secret | None
    gemini_model: str | None

    @property
    def is_production(self) -> bool:
        return self.app_env is AppEnv.PRODUCTION

    @property
    def ai_enabled(self) -> bool:
        return self.gemini_api_key is not None and self.gemini_model is not None

    def cursor_secrets(self) -> list[bytes] | None:
        """Signing keys for pagination cursors (current first), or None to use a throwaway key."""
        if self.session_secret is None:
            return None
        keys = [derive_secret(self.session_secret, "cursor")]
        if self.previous_session_secret is not None:
            keys.append(derive_secret(self.previous_session_secret, "cursor"))
        return keys

    def safe_summary(self) -> dict[str, object]:
        """What is configured, with no secret values: safe to log at startup."""
        return {
            "app_env": self.app_env.value,
            "data_mode": self.data_mode.value,
            "log_level": self.log_level,
            "trusted_proxy_hops": self.trusted_proxy_hops,
            "session_secret": "set" if self.session_secret else "unset",
            "database": "set" if self.database_url else "unset",
            "neo4j": "set" if self.neo4j_uri else "unset",
            "redis": "set" if self.redis_url else "unset",
            "github_token": "set" if self.github_token else "unset",
            "ai": "enabled" if self.ai_enabled else "disabled",
        }


def _get(environ: Mapping[str, str], name: str) -> str | None:
    value = environ.get(name, "").strip()
    return value or None


def _flag(environ: Mapping[str, str], name: str, problems: list[str]) -> bool:
    raw = environ.get(name, "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    problems.append(f"{name} must be true or false")
    return False


def _secret(
    environ: Mapping[str, str], name: str, problems: list[str], *, min_length: int = 1
) -> Secret | None:
    value = _get(environ, name)
    if value is None:
        return None
    if len(value) < min_length:
        problems.append(f"{name} must be at least {min_length} characters")
        return None
    return Secret(value)


def load_settings(environ: Mapping[str, str]) -> Settings:
    """Validate ``environ`` (usually ``os.environ``) and return typed settings or raise ConfigError."""
    problems: list[str] = []

    raw_env = (_get(environ, "APP_ENV") or "development").lower()
    try:
        app_env = AppEnv(raw_env)
    except ValueError:
        problems.append(f"APP_ENV must be one of {[e.value for e in AppEnv]}")
        app_env = AppEnv.PRODUCTION  # fail towards the strict rules while collecting problems
    production = app_env is AppEnv.PRODUCTION

    log_level = (_get(environ, "LOG_LEVEL") or "info").lower()
    if log_level not in _LOG_LEVELS:
        problems.append(f"LOG_LEVEL must be one of {list(_LOG_LEVELS)}")
        log_level = "info"

    allow_seed = _flag(environ, "ALLOW_SEED_DATA", problems)
    raw_mode = (_get(environ, "DATA_MODE") or ("database" if production else "seed")).lower()
    try:
        data_mode = DataMode(raw_mode)
    except ValueError:
        problems.append(f"DATA_MODE must be one of {[m.value for m in DataMode]}")
        data_mode = DataMode.DATABASE
    if production and data_mode is DataMode.SEED and not allow_seed:
        problems.append("production refuses DATA_MODE=seed unless ALLOW_SEED_DATA=true")

    session_secret = _secret(environ, "SESSION_SECRET", problems, min_length=MIN_SECRET_LENGTH)
    previous = _secret(environ, "SESSION_SECRET_PREVIOUS", problems, min_length=MIN_SECRET_LENGTH)
    if production and session_secret is None:
        problems.append(f"SESSION_SECRET is required in production (at least {MIN_SECRET_LENGTH} characters)")
    if previous is not None and session_secret is None:
        problems.append("SESSION_SECRET_PREVIOUS needs SESSION_SECRET")
    if previous is not None and session_secret is not None and previous == session_secret:
        problems.append("SESSION_SECRET_PREVIOUS must differ from SESSION_SECRET")

    raw_hops = _get(environ, "TRUSTED_PROXY_HOPS") or "0"
    hops = int(raw_hops) if re.fullmatch(r"[0-9]{1,2}", raw_hops) else -1
    if not 0 <= hops <= 5:
        problems.append("TRUSTED_PROXY_HOPS must be an integer from 0 to 5")
        hops = 0

    urls: dict[str, str | None] = {}
    for name, schemes in _URL_SCHEMES.items():
        value = _get(environ, name)
        if value is not None and not value.lower().startswith(schemes):
            problems.append(f"{name} must start with one of {list(schemes)}")  # never echo the value
            value = None
        if value is None and data_mode is DataMode.DATABASE:
            problems.append(f"{name} is required when DATA_MODE=database")
        urls[name] = value
    neo4j_password = _secret(environ, "NEO4J_PASSWORD", problems)
    if data_mode is DataMode.DATABASE and neo4j_password is None:
        problems.append("NEO4J_PASSWORD is required when DATA_MODE=database")

    github_token = _secret(environ, "GITHUB_TOKEN", problems)
    gemini_key = _secret(environ, "GEMINI_API_KEY", problems)
    gemini_model = _get(environ, "GEMINI_MODEL")
    if gemini_model is not None and not _MODEL_RE.fullmatch(gemini_model):
        problems.append("GEMINI_MODEL is not a valid model id")
        gemini_model = None
    if gemini_key is not None and gemini_model is None:
        problems.append("GEMINI_MODEL must be set when GEMINI_API_KEY is set (no model id is assumed)")

    if problems:
        raise ConfigError("invalid configuration: " + "; ".join(problems))

    return Settings(
        app_env=app_env,
        log_level=log_level,
        data_mode=data_mode,
        session_secret=session_secret,
        previous_session_secret=previous,
        trusted_proxy_hops=hops,
        database_url=Secret(urls["DATABASE_URL"]) if urls["DATABASE_URL"] else None,
        neo4j_uri=urls["NEO4J_URI"],
        neo4j_username=_get(environ, "NEO4J_USERNAME"),
        neo4j_password=neo4j_password,
        redis_url=Secret(urls["REDIS_URL"]) if urls["REDIS_URL"] else None,
        github_token=github_token,
        gemini_api_key=gemini_key,
        gemini_model=gemini_model,
    )
