"""Postgres persistence for OAuth state and linked Bring! accounts.

Everything that grants access is stored so that a database dump alone is
useless: our own tokens, codes and authorization ids only as SHA-256 hashes,
the Bring! refresh token Fernet-encrypted with ``TOKEN_ENCRYPTION_KEY``. The
Bring! password is never stored -- it is used once, at login.

The schema is created on startup (``CREATE TABLE IF NOT EXISTS``); there is
no migration tool yet because there is only this one version.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import asyncpg
from cryptography.fernet import Fernet, InvalidToken

_LOGGER = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id            text PRIMARY KEY,           -- Bring! user uuid
    email         text NOT NULL,
    public_uuid   text NOT NULL,
    refresh_token bytea NOT NULL,             -- Fernet-encrypted
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id  text PRIMARY KEY,
    info       jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS pending_authorizations (
    id_hash    text PRIMARY KEY,
    client_id  text NOT NULL,
    params     jsonb NOT NULL,
    expires_at double precision NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_codes (
    code_hash  text PRIMARY KEY,
    client_id  text NOT NULL,
    account_id text NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    data       jsonb NOT NULL,
    expires_at double precision NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens (
    token_hash text PRIMARY KEY,
    kind       text NOT NULL CHECK (kind IN ('access', 'refresh')),
    family     text NOT NULL,                 -- one grant: its access + refresh tokens
    client_id  text NOT NULL,
    account_id text NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    scopes     text[] NOT NULL,
    resource   text,
    expires_at bigint
);
CREATE INDEX IF NOT EXISTS tokens_family_idx ON tokens (family);
"""


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class Account:
    id: str
    email: str
    public_uuid: str
    refresh_token: str


@dataclass(frozen=True)
class StoredToken:
    kind: str
    family: str
    client_id: str
    account_id: str
    scopes: list[str]
    resource: str | None
    expires_at: int | None


class Store:
    def __init__(self, database_url: str, encryption_key: str) -> None:
        self._dsn = database_url
        try:
            self._fernet = Fernet(encryption_key.encode())
        except ValueError as exc:
            raise ValueError(
                "TOKEN_ENCRYPTION_KEY must be a Fernet key "
                "(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
            ) from exc
        self._pool: asyncpg.Pool | None = None

    # ---- lifecycle ----------------------------------------------------
    async def start(self) -> None:
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
        async with self._pool.acquire() as conn:
            await conn.execute(_SCHEMA)
        _LOGGER.info("Database ready")

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
        self._pool = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Store has not been started")
        return self._pool

    async def ping(self) -> bool:
        try:
            await self.pool.fetchval("SELECT 1")
            return True
        except Exception:  # noqa: BLE001 - readiness only reports, never raises
            return False

    async def purge_expired(self) -> None:
        now = time.time()
        await self.pool.execute("DELETE FROM pending_authorizations WHERE expires_at < $1", now)
        await self.pool.execute("DELETE FROM auth_codes WHERE expires_at < $1", now)
        await self.pool.execute(
            "DELETE FROM tokens WHERE expires_at IS NOT NULL AND expires_at < $1", int(now)
        )

    # ---- accounts -----------------------------------------------------
    async def upsert_account(self, account: Account) -> None:
        await self.pool.execute(
            """
            INSERT INTO accounts (id, email, public_uuid, refresh_token)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email,
                public_uuid = EXCLUDED.public_uuid,
                refresh_token = EXCLUDED.refresh_token, updated_at = now()
            """,
            account.id,
            account.email,
            account.public_uuid,
            self._fernet.encrypt(account.refresh_token.encode()),
        )

    async def update_bring_refresh_token(self, account_id: str, refresh_token: str) -> None:
        await self.pool.execute(
            "UPDATE accounts SET refresh_token = $2, updated_at = now() WHERE id = $1",
            account_id,
            self._fernet.encrypt(refresh_token.encode()),
        )

    async def get_account(self, account_id: str) -> Account | None:
        row = await self.pool.fetchrow("SELECT * FROM accounts WHERE id = $1", account_id)
        if row is None:
            return None
        try:
            refresh_token = self._fernet.decrypt(bytes(row["refresh_token"])).decode()
        except InvalidToken:
            _LOGGER.error("Cannot decrypt the Bring! token of account %s (key changed?)", row["id"])
            return None
        return Account(row["id"], row["email"], row["public_uuid"], refresh_token)

    # ---- OAuth clients ------------------------------------------------
    async def save_client(self, client_id: str, info: dict[str, Any]) -> None:
        await self.pool.execute(
            "INSERT INTO oauth_clients (client_id, info) VALUES ($1, $2) "
            "ON CONFLICT (client_id) DO UPDATE SET info = EXCLUDED.info",
            client_id,
            json.dumps(info),
        )

    async def get_client(self, client_id: str) -> dict[str, Any] | None:
        raw = await self.pool.fetchval("SELECT info FROM oauth_clients WHERE client_id = $1", client_id)
        return json.loads(raw) if raw is not None else None

    # ---- pending authorizations (between /authorize and the login form) --
    async def save_pending(self, pending_id: str, client_id: str, params: dict[str, Any], ttl: int) -> None:
        await self.pool.execute(
            "INSERT INTO pending_authorizations (id_hash, client_id, params, expires_at) "
            "VALUES ($1, $2, $3, $4)",
            hash_secret(pending_id),
            client_id,
            json.dumps(params),
            time.time() + ttl,
        )

    async def get_pending(self, pending_id: str) -> tuple[str, dict[str, Any]] | None:
        row = await self.pool.fetchrow(
            "SELECT client_id, params FROM pending_authorizations WHERE id_hash = $1 AND expires_at >= $2",
            hash_secret(pending_id),
            time.time(),
        )
        return (row["client_id"], json.loads(row["params"])) if row else None

    async def delete_pending(self, pending_id: str) -> None:
        await self.pool.execute(
            "DELETE FROM pending_authorizations WHERE id_hash = $1", hash_secret(pending_id)
        )

    # ---- authorization codes ------------------------------------------
    async def save_code(
        self, code: str, client_id: str, account_id: str, data: dict[str, Any], expires_at: float
    ) -> None:
        await self.pool.execute(
            "INSERT INTO auth_codes (code_hash, client_id, account_id, data, expires_at) "
            "VALUES ($1, $2, $3, $4, $5)",
            hash_secret(code),
            client_id,
            account_id,
            json.dumps(data),
            expires_at,
        )

    async def get_code(self, code: str) -> tuple[str, str, dict[str, Any], float] | None:
        row = await self.pool.fetchrow(
            "SELECT client_id, account_id, data, expires_at FROM auth_codes WHERE code_hash = $1",
            hash_secret(code),
        )
        if row is None:
            return None
        return row["client_id"], row["account_id"], json.loads(row["data"]), row["expires_at"]

    async def take_code(self, code: str) -> bool:
        """Delete a code; True only for the caller that actually removed it (single use)."""
        result = await self.pool.execute("DELETE FROM auth_codes WHERE code_hash = $1", hash_secret(code))
        return result.endswith(" 1")

    # ---- tokens -------------------------------------------------------
    async def save_token(self, token: str, stored: StoredToken) -> None:
        await self.pool.execute(
            "INSERT INTO tokens (token_hash, kind, family, client_id, account_id, scopes, resource, expires_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            hash_secret(token),
            stored.kind,
            stored.family,
            stored.client_id,
            stored.account_id,
            stored.scopes,
            stored.resource,
            stored.expires_at,
        )

    async def get_token(self, token: str, kind: str) -> StoredToken | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM tokens WHERE token_hash = $1 AND kind = $2", hash_secret(token), kind
        )
        if row is None:
            return None
        if row["expires_at"] is not None and row["expires_at"] < time.time():
            return None
        return StoredToken(
            kind=row["kind"],
            family=row["family"],
            client_id=row["client_id"],
            account_id=row["account_id"],
            scopes=list(row["scopes"]),
            resource=row["resource"],
            expires_at=row["expires_at"],
        )

    async def take_token(self, token: str, kind: str) -> bool:
        result = await self.pool.execute(
            "DELETE FROM tokens WHERE token_hash = $1 AND kind = $2", hash_secret(token), kind
        )
        return result.endswith(" 1")

    async def revoke_family(self, family: str) -> None:
        await self.pool.execute("DELETE FROM tokens WHERE family = $1", family)

    async def revoke_account(self, account_id: str) -> None:
        """Drop every token of an account, so its clients have to sign in again."""
        await self.pool.execute("DELETE FROM tokens WHERE account_id = $1", account_id)
