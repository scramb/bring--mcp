"""ASGI application: OAuth, login page, health endpoints and the MCP endpoint.

The MCP SDK builds the OAuth routes (metadata, ``/authorize``, ``/token``,
``/register``, ``/revoke``) and guards the MCP path with bearer-token auth;
``/login``, ``/healthz`` and ``/readyz`` are added here as unauthenticated
custom routes. The store and the Bring! sessions are bound to *this* app's
lifespan, with the SDK's Streamable HTTP session manager nested inside.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount

from .bring_client import BringSessions
from .config import Config
from .oauth import LOGIN_PATH, BringOAuthProvider
from .server import build_mcp
from .store import Store

_LOGGER = logging.getLogger(__name__)

HEALTH_PATH = "/healthz"
READY_PATH = "/readyz"


def create_app(config: Config) -> Starlette:
    store = Store(config.database_url, config.token_encryption_key)
    sessions = BringSessions(store)
    provider = BringOAuthProvider(config, store, sessions)
    mcp = build_mcp(config, store, sessions, provider)

    @mcp.custom_route(LOGIN_PATH, methods=["GET", "POST"])
    async def login(request: Request) -> Response:
        return await provider.login_page(request)

    @mcp.custom_route(HEALTH_PATH, methods=["GET"])
    async def healthz(_request: Request) -> Response:
        return PlainTextResponse("ok")

    @mcp.custom_route(READY_PATH, methods=["GET"])
    async def readyz(_request: Request) -> Response:
        ready = await store.ping()
        return JSONResponse({"ready": ready}, status_code=200 if ready else 503)

    mcp_app = mcp.streamable_http_app(
        streamable_http_path=config.mcp_path,
        json_response=config.json_response,
        stateless_http=True,
        # Not a localhost bind: the SDK's DNS-rebinding guard stays off, the
        # Host header is the public name behind the gateway.
        host=config.host,
    )

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        await store.start()
        await sessions.start()
        try:
            async with mcp_app.router.lifespan_context(mcp_app):
                yield
        finally:
            await sessions.close()
            await store.close()

    return Starlette(routes=[Mount("/", app=mcp_app)], lifespan=lifespan)
