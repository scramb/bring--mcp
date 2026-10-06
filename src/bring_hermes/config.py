"""Runtime configuration, loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def _get(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _require(name: str) -> str:
    value = _get(name)
    if not value:
        raise ConfigError(f"Required environment variable {name!r} is not set")
    return value


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(name: str, default: int) -> int:
    raw = _get(name, str(default)) or str(default)
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class Config:
    """Validated application configuration."""

    public_url: str
    database_url: str
    token_encryption_key: str
    host: str
    port: int
    mcp_path: str
    json_response: bool
    log_level: str
    access_token_ttl: int
    refresh_token_ttl: int

    @property
    def resource_url(self) -> str:
        """The URL clients connect to; the OAuth resource identifier."""
        return self.public_url + self.mcp_path

    @classmethod
    def from_env(cls) -> Config:
        public_url = _require("PUBLIC_URL").rstrip("/")
        if not public_url.startswith(("https://", "http://")):
            raise ConfigError("PUBLIC_URL must start with https:// (or http:// for local use)")

        mcp_path = _get("MCP_PATH", "/mcp") or "/mcp"
        if not mcp_path.startswith("/"):
            mcp_path = "/" + mcp_path

        return cls(
            public_url=public_url,
            database_url=_require("DATABASE_URL"),
            token_encryption_key=_require("TOKEN_ENCRYPTION_KEY"),
            host=_get("HOST", "0.0.0.0") or "0.0.0.0",
            port=_as_int("PORT", 8080),
            mcp_path=mcp_path,
            json_response=_as_bool(_get("MCP_JSON_RESPONSE"), True),
            log_level=(_get("LOG_LEVEL", "INFO") or "INFO").upper(),
            access_token_ttl=_as_int("ACCESS_TOKEN_TTL", 3600),
            refresh_token_ttl=_as_int("REFRESH_TOKEN_TTL", 90 * 24 * 3600),
        )
