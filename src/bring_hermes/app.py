"""ASGI application: API-key auth, health endpoints, and the MCP mount.

The Bring! client is a process-wide singleton whose lifecycle is bound to *this*
app's lifespan. The FastMCP Streamable HTTP session manager is nested inside it
so both start and stop cleanly together. (In stateless mode FastMCP's own
``lifespan`` would run per request, which is why login lives here instead.)
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from .bring_client import BringClient
from .config import Config
from .server import build_mcp

_LOGGER = logging.getLogger(__name__)

HEALTH_PATH = "/healthz"
READY_PATH = "/readyz"


class ApiKeyAuthMiddleware:
    """Reject requests without a valid API key, except for health endpoints.

    Accepts either ``Authorization: Bearer <key>`` or ``X-API-Key: <key>``.
    """

    def __init__(
        self, app: ASGIApp, *, api_keys: tuple[str, ...], exempt_paths: frozenset[str]
    ) -> None:
        self.app = app
        self._api_keys = tuple(api_keys)
        self._exempt_paths = exempt_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self._exempt_paths:
            await self.app(scope, receive, send)
            return
        if self._authorized(Headers(scope=scope)):
            await self.app(scope, receive, send)
            return
        await _send_unauthorized(send)

    def _authorized(self, headers: Headers) -> bool:
        provided: str | None = None
        authorization = headers.get("authorization")
        if authorization and authorization[:7].lower() == "bearer ":
            provided = authorization[7:].strip()
        if not provided:
            provided = headers.get("x-api-key")
        if not provided:
            return False
        return any(hmac.compare_digest(provided, key) for key in self._api_keys)


async def _send_unauthorized(send: Send) -> None:
    body = b'{"error":"unauthorized"}'
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b'Bearer realm="bring-hermes"'),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def create_app(config: Config) -> Starlette:
    client = BringClient(config)
    mcp = build_mcp(config, client)
    mcp_app = mcp.streamable_http_app()

    async def healthz(_request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    async def readyz(_request: Request) -> JSONResponse:
        ready = await client.ensure_ready()
        return JSONResponse({"ready": ready}, status_code=200 if ready else 503)

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        await client.start()
        try:
            # Run the MCP session manager for the lifetime of the app.
            async with mcp_app.router.lifespan_context(mcp_app):
                yield
        finally:
            await client.close()

    exempt = frozenset({HEALTH_PATH, READY_PATH})
    return Starlette(
        routes=[
            Route(HEALTH_PATH, healthz, methods=["GET"]),
            Route(READY_PATH, readyz, methods=["GET"]),
            Mount("/", app=mcp_app),
        ],
        lifespan=lifespan,
        middleware=[
            Middleware(
                ApiKeyAuthMiddleware,
                api_keys=config.api_keys,
                exempt_paths=exempt,
            )
        ],
    )
