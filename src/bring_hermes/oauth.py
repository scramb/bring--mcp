"""OAuth 2.1 authorization server whose login is a Bring! sign-in.

Bring! itself offers no OAuth, so this server is the authorization server for
its own MCP endpoint (the MCP SDK provides metadata, dynamic client
registration, PKCE and the token endpoint around this provider):

1. ``/authorize`` (SDK) validates the client and calls :meth:`authorize`,
   which parks the request under a random id and redirects to ``/login``.
2. ``/login`` shows a form; on POST the credentials go to Bring! once. On
   success the account is linked, an authorization code is issued and the
   browser is sent back to the client's ``redirect_uri``.
3. ``/token`` (SDK) exchanges the code (PKCE-checked by the SDK) for an
   access and a refresh token whose subject is the Bring! user uuid.

Any Bring! account may sign in; each user only ever reaches their own lists.
"""

from __future__ import annotations

import html
import logging
import secrets
import time
from collections import deque
from typing import Any

from bring_api.exceptions import BringAuthException, BringException
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from .bring_client import BringSessions
from .config import Config
from .store import Store, StoredToken

_LOGGER = logging.getLogger(__name__)

SCOPE = "bring"
LOGIN_PATH = "/login"
PENDING_TTL = 600  # seconds the login form stays valid
CODE_TTL = 300


class BringOAuthProvider:
    """Implements ``OAuthAuthorizationServerProvider`` on top of :class:`Store`."""

    def __init__(self, config: Config, store: Store, sessions: BringSessions) -> None:
        self._config = config
        self._store = store
        self._sessions = sessions
        self._failures = _FailureLimiter(max_failures=5, window=600)

    # ---- clients ------------------------------------------------------
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        info = await self._store.get_client(client_id)
        return OAuthClientInformationFull.model_validate(info) if info else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if client_info.client_id is None:
            raise ValueError("client_id is required")
        await self._store.save_client(client_info.client_id, client_info.model_dump(mode="json"))
        _LOGGER.info("Registered OAuth client %s (%s)", client_info.client_id, client_info.client_name)

    # ---- authorization ------------------------------------------------
    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if client.client_id is None:
            raise AuthorizeError(error="invalid_request", error_description="client_id is missing")
        await self._store.purge_expired()
        pending_id = secrets.token_urlsafe(32)
        await self._store.save_pending(
            pending_id, client.client_id, params.model_dump(mode="json"), PENDING_TTL
        )
        return f"{self._config.public_url}{LOGIN_PATH}?request={pending_id}"

    async def login_page(self, request: Request) -> Response:
        if request.method == "GET":
            pending_id = request.query_params.get("request", "")
            if await self._store.get_pending(pending_id) is None:
                return _expired_page()
            return _login_page(pending_id)

        form = await request.form()
        pending_id = str(form.get("request", ""))
        email = str(form.get("email", "")).strip()
        password = str(form.get("password", ""))
        pending = await self._store.get_pending(pending_id)
        if pending is None:
            return _expired_page()
        client_id, raw_params = pending
        params = AuthorizationParams.model_validate(raw_params)

        if not email or not password:
            return _login_page(pending_id, email, "Bitte E-Mail und Passwort eingeben.")
        if self._failures.blocked(email):
            return _login_page(pending_id, email, "Zu viele Fehlversuche. Bitte später erneut versuchen.", 429)
        try:
            account = await self._sessions.login(email, password)
        except BringAuthException:
            self._failures.record(email)
            return _login_page(pending_id, email, "E-Mail oder Passwort ist falsch.", 401)
        except BringException:
            _LOGGER.exception("Bring! login failed")
            return _login_page(pending_id, email, "Bring! ist gerade nicht erreichbar.", 502)

        await self._store.delete_pending(pending_id)
        code = secrets.token_urlsafe(32)
        auth_code = AuthorizationCode(
            code=code,
            scopes=params.scopes or [SCOPE],
            expires_at=time.time() + CODE_TTL,
            client_id=client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
            subject=account.id,
        )
        await self._store.save_code(
            code, client_id, account.id, auth_code.model_dump(mode="json"), auth_code.expires_at
        )
        target = construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)
        return RedirectResponse(target, status_code=302)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        row = await self._store.get_code(authorization_code)
        if row is None:
            return None
        client_id, _account_id, data, _expires = row
        if client_id != client.client_id:
            return None
        return AuthorizationCode.model_validate(data)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        if not await self._store.take_code(authorization_code.code):
            raise TokenError(error="invalid_grant", error_description="authorization code already used")
        if authorization_code.subject is None:
            raise TokenError(error="invalid_grant", error_description="authorization code has no subject")
        return await self._issue(
            client_id=authorization_code.client_id,
            account_id=authorization_code.subject,
            scopes=authorization_code.scopes,
            resource=authorization_code.resource,
            family=secrets.token_urlsafe(16),
        )

    # ---- refresh ------------------------------------------------------
    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        stored = await self._store.get_token(refresh_token, "refresh")
        if stored is None or stored.client_id != client.client_id:
            return None
        return _RefreshToken(
            token=refresh_token,
            client_id=stored.client_id,
            scopes=stored.scopes,
            expires_at=stored.expires_at,
            resource=stored.resource,
            subject=stored.account_id,
            family=stored.family,
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        assert isinstance(refresh_token, _RefreshToken)
        # Rotation: a refresh token works once. A second use (a parallel
        # refresh, or a replay) is rejected without revoking the grant, so a
        # client racing itself does not lose its connection.
        if not await self._store.take_token(refresh_token.token, "refresh"):
            raise TokenError(error="invalid_grant", error_description="refresh token already used")
        # Drop the access token(s) of the old pair; the grant continues with a new pair.
        await self._store.revoke_family(refresh_token.family)
        assert refresh_token.subject is not None
        return await self._issue(
            client_id=refresh_token.client_id,
            account_id=refresh_token.subject,
            scopes=scopes or refresh_token.scopes,
            resource=refresh_token.resource,
            family=refresh_token.family,
        )

    # ---- access -------------------------------------------------------
    async def load_access_token(self, token: str) -> AccessToken | None:
        stored = await self._store.get_token(token, "access")
        if stored is None:
            return None
        return AccessToken(
            token=token,
            client_id=stored.client_id,
            scopes=stored.scopes,
            expires_at=stored.expires_at,
            resource=stored.resource,
            subject=stored.account_id,
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        kind = "refresh" if isinstance(token, RefreshToken) else "access"
        stored = await self._store.get_token(token.token, kind)
        if stored is not None:
            await self._store.revoke_family(stored.family)

    async def exchange_identity_assertion(self, client: Any, params: Any) -> OAuthToken:
        raise TokenError(error="unsupported_grant_type", error_description="not supported")

    # ---- helpers ------------------------------------------------------
    async def _issue(
        self, *, client_id: str, account_id: str, scopes: list[str], resource: str | None, family: str
    ) -> OAuthToken:
        now = int(time.time())
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        for kind, token, ttl in (
            ("access", access, self._config.access_token_ttl),
            ("refresh", refresh, self._config.refresh_token_ttl),
        ):
            stored = StoredToken(
                kind=kind,
                family=family,
                client_id=client_id,
                account_id=account_id,
                scopes=scopes,
                resource=resource,
                expires_at=now + ttl,
            )
            await self._store.save_token(token, stored)
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=self._config.access_token_ttl,
            refresh_token=refresh,
            scope=" ".join(scopes),
        )


class _RefreshToken(RefreshToken):
    family: str


class _FailureLimiter:
    """In-memory limit on failed logins per e-mail address (one replica)."""

    def __init__(self, *, max_failures: int, window: float) -> None:
        self._max = max_failures
        self._window = window
        self._failures: dict[str, deque[float]] = {}

    def _recent(self, key: str) -> deque[float]:
        entries = self._failures.setdefault(key.casefold(), deque())
        cutoff = time.monotonic() - self._window
        while entries and entries[0] < cutoff:
            entries.popleft()
        return entries

    def blocked(self, key: str) -> bool:
        return len(self._recent(key)) >= self._max

    def record(self, key: str) -> None:
        self._recent(key).append(time.monotonic())


# ---- pages --------------------------------------------------------------

_STYLE = """
body{font-family:system-ui,-apple-system,sans-serif;background:#f4f1ea;color:#222;
display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}
main{background:#fff;padding:2rem;border-radius:12px;box-shadow:0 2px 12px rgba(0,0,0,.08);
width:100%;max-width:22rem}
h1{font-size:1.25rem;margin:0 0 .25rem}p{color:#555;font-size:.9rem;margin:0 0 1.25rem}
label{display:block;font-size:.85rem;margin:.75rem 0 .25rem}
input{width:100%;box-sizing:border-box;padding:.6rem;border:1px solid #ccc;border-radius:6px;font-size:1rem}
button{margin-top:1.25rem;width:100%;padding:.7rem;border:0;border-radius:6px;background:#ee524f;
color:#fff;font-size:1rem;cursor:pointer}
.error{background:#fdecea;color:#a12622;padding:.6rem;border-radius:6px;font-size:.9rem}
small{display:block;color:#777;margin-top:1rem;font-size:.75rem}
"""


def _page(body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f'<!doctype html><html lang="de"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Bring! verbinden</title><style>{_STYLE}</style></head>"
        f"<body><main>{body}</main></body></html>",
        status_code=status,
        headers={
            "Cache-Control": "no-store",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self' https:",
            "Referrer-Policy": "no-referrer",
        },
    )


def _login_page(pending_id: str, email: str = "", error: str | None = None, status: int = 200) -> HTMLResponse:
    error_html = f'<div class="error">{html.escape(error)}</div>' if error else ""
    return _page(
        f"""<h1>Mit Bring! anmelden</h1>
<p>Verbinde dein Bring!-Konto, damit dein Assistent deine Einkaufslisten bearbeiten kann.</p>
{error_html}
<form method="post" action="{LOGIN_PATH}">
<input type="hidden" name="request" value="{html.escape(pending_id)}">
<label for="email">E-Mail</label>
<input id="email" name="email" type="email" autocomplete="username" required value="{html.escape(email)}">
<label for="password">Passwort</label>
<input id="password" name="password" type="password" autocomplete="current-password" required>
<button type="submit">Verbinden</button>
</form>
<small>Dein Passwort wird nur zur Anmeldung an Bring! weitergegeben und nicht gespeichert.</small>""",
        status,
    )


def _expired_page() -> HTMLResponse:
    return _page(
        "<h1>Anmeldung abgelaufen</h1><p>Bitte starte die Verbindung in deinem Assistenten neu.</p>",
        400,
    )
