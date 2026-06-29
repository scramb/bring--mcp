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


@dataclass(frozen=True)
class Config:
    """Validated application configuration."""

    bring_email: str
    bring_password: str
    api_keys: tuple[str, ...]
    default_list: str | None
    host: str
    port: int
    mcp_path: str
    json_response: bool
    log_level: str

    @classmethod
    def from_env(cls) -> "Config":
        raw_keys = _get("MCP_API_KEY", "") or ""
        api_keys = tuple(k.strip() for k in raw_keys.split(",") if k.strip())
        if not api_keys:
            raise ConfigError("Required environment variable 'MCP_API_KEY' is not set")

        mcp_path = _get("MCP_PATH", "/mcp") or "/mcp"
        if not mcp_path.startswith("/"):
            mcp_path = "/" + mcp_path

        raw_port = _get("PORT", "8080") or "8080"
        try:
            port = int(raw_port)
        except ValueError as exc:
            raise ConfigError(f"PORT must be an integer, got {raw_port!r}") from exc

        return cls(
            bring_email=_require("BRING_EMAIL"),
            bring_password=_require("BRING_PASSWORD"),
            api_keys=api_keys,
            default_list=_get("BRING_DEFAULT_LIST"),
            host=_get("HOST", "0.0.0.0") or "0.0.0.0",
            port=port,
            mcp_path=mcp_path,
            json_response=_as_bool(_get("MCP_JSON_RESPONSE"), True),
            log_level=(_get("LOG_LEVEL", "INFO") or "INFO").upper(),
        )
