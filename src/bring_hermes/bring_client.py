"""Per-user Bring! sessions on top of ``bring-api``.

Every MCP user links their own Bring! account through the OAuth login page.
The password is used exactly once, for :meth:`BringSessions.login`; what is
kept (encrypted, see :mod:`store`) is the Bring! refresh token. A session is
restored from it on demand and cached in memory per account.

``bring-api`` keeps the refresh token in a private attribute and discards
the rotated one it gets back from a refresh. :class:`_PersistingBring` closes
that gap: it adopts the new token and hands it to the store, so a pod restart
resumes with a token Bring! still accepts. This touches a private attribute,
which is why ``bring-api`` is pinned to an exact version.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import TypeVar

import aiohttp
from bring_api import (
    Bring,
    BringItem,
    BringItemOperation,
    BringList,
    BringPurchase,
    BringTemplate,
)
from bring_api.exceptions import BringAuthException

from .scaling import compute_factor, scale_quantity
from .store import Account, Store

_LOGGER = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

T = TypeVar("T")


class BringSessionExpired(Exception):
    """The stored Bring! session is gone; the user has to sign in again."""


class _PersistingBring(Bring):
    def __init__(
        self,
        session: aiohttp.ClientSession,
        mail: str,
        password: str,
        on_refresh: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(session, mail, password)
        self._on_refresh = on_refresh

    @property
    def refresh_token(self) -> str | None:
        return self._Bring__refresh_token

    @refresh_token.setter
    def refresh_token(self, value: str) -> None:
        self._Bring__refresh_token = value

    async def retrieve_new_access_token(self, refresh_token: str | None = None):
        data = await super().retrieve_new_access_token(refresh_token)
        if data.refresh_token and data.refresh_token != self.refresh_token:
            self.refresh_token = data.refresh_token
            if self._on_refresh is not None:
                await self._on_refresh(data.refresh_token)
        return data


class BringSessions:
    """Logs users in and hands out one :class:`BringAccount` per account."""

    def __init__(self, store: Store) -> None:
        self._store = store
        self._http: aiohttp.ClientSession | None = None
        self._accounts: dict[str, BringAccount] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def start(self) -> None:
        self._http = aiohttp.ClientSession()

    async def close(self) -> None:
        if self._http is not None:
            await self._http.close()
        self._http = None
        self._accounts.clear()

    @property
    def http(self) -> aiohttp.ClientSession:
        if self._http is None:
            raise RuntimeError("BringSessions has not been started")
        return self._http

    async def login(self, email: str, password: str) -> Account:
        """Sign in with Bring! and store the account. Raises BringAuthException."""
        bring = _PersistingBring(self.http, email, password)
        await bring.login()
        if not bring.refresh_token:
            raise BringAuthException("Bring! returned no refresh token")
        account = Account(
            id=bring.uuid, email=email, public_uuid=bring.public_uuid, refresh_token=bring.refresh_token
        )
        await self._store.upsert_account(account)
        # The fresh login is a ready session; keep it instead of restoring later.
        bring._on_refresh = self._persister(account.id)
        self._accounts[account.id] = BringAccount(bring, self, account.id)
        _LOGGER.info("Linked Bring! account %s", account.id)
        return account

    async def get(self, account_id: str) -> BringAccount:
        if (cached := self._accounts.get(account_id)) is not None:
            return cached
        lock = self._locks.setdefault(account_id, asyncio.Lock())
        async with lock:
            if (cached := self._accounts.get(account_id)) is not None:
                return cached
            account = await self._store.get_account(account_id)
            if account is None:
                raise BringSessionExpired("No Bring! account is linked to this token")
            bring = await self._restore(account)
            client = BringAccount(bring, self, account_id)
            self._accounts[account_id] = client
            return client

    def forget(self, account_id: str) -> None:
        self._accounts.pop(account_id, None)

    def _persister(self, account_id: str) -> Callable[[str], Awaitable[None]]:
        async def persist(refresh_token: str) -> None:
            await self._store.update_bring_refresh_token(account_id, refresh_token)

        return persist

    async def _restore(self, account: Account) -> _PersistingBring:
        bring = _PersistingBring(self.http, account.email, "", self._persister(account.id))
        bring.uuid = account.id
        bring.public_uuid = account.public_uuid
        bring.headers["X-BRING-USER-UUID"] = account.id
        bring.headers["X-BRING-PUBLIC-USER-UUID"] = account.public_uuid
        bring.refresh_token = account.refresh_token
        try:
            await bring.retrieve_new_access_token()
        except BringAuthException as exc:
            raise BringSessionExpired("The Bring! session has expired") from exc
        # The rest of what Bring.login() does after obtaining a token.
        locale = (await bring.get_user_account()).userLocale
        bring.headers["X-BRING-COUNTRY"] = locale.country
        bring.user_locale = bring.map_user_language_to_locale(locale)
        await bring.reload_user_list_settings()
        await bring.reload_article_translations()
        _LOGGER.info("Restored Bring! session for account %s", account.id)
        return bring


class BringAccount:
    """Login-aware facade over the Bring! API for one linked account."""

    def __init__(self, bring: _PersistingBring, sessions: BringSessions, account_id: str) -> None:
        self._bring = bring
        self._sessions = sessions
        self._account_id = account_id
        self._lists: list[BringList] = []

    async def _call(self, factory: Callable[[], Awaitable[T]]) -> T:
        # bring-api refreshes an expired access token and retries a 401 once
        # by itself; an auth error reaching us means the refresh token is dead.
        try:
            return await factory()
        except BringAuthException as exc:
            self._sessions.forget(self._account_id)
            raise BringSessionExpired("The Bring! session has expired") from exc

    # ---- list resolution ----------------------------------------------
    async def get_lists(self, *, refresh: bool = False) -> list[BringList]:
        if self._lists and not refresh:
            return self._lists
        response = await self._call(lambda: self._bring.load_lists())
        self._lists = list(response.lists)
        return self._lists

    async def resolve_list_uuid(self, list_ref: str | None) -> str:
        ref = (list_ref or "").strip()
        lists = await self.get_lists()
        if not ref:
            if not lists:
                raise ValueError("No shopping lists exist on this Bring! account")
            return lists[0].listUuid
        if _UUID_RE.match(ref):
            return ref
        match = _match_by_name(lists, ref)
        if match is None:
            lists = await self.get_lists(refresh=True)
            match = _match_by_name(lists, ref)
        if match is None:
            available = ", ".join(item.name for item in lists) or "(none)"
            raise ValueError(f"Shopping list {ref!r} not found. Available lists: {available}")
        return match

    # ---- operations ---------------------------------------------------
    async def list_items(self, list_ref: str | None) -> tuple[str, list[BringPurchase]]:
        uuid = await self.resolve_list_uuid(list_ref)
        response = await self._call(lambda: self._bring.get_list(uuid))
        return uuid, list(response.items.purchase)

    async def add_item(self, name: str, quantity: str, list_ref: str | None) -> str:
        uuid = await self.resolve_list_uuid(list_ref)
        await self._call(lambda: self._bring.save_item(uuid, name, quantity or ""))
        return uuid

    async def add_items(self, items: list[BringItem], list_ref: str | None) -> str:
        uuid = await self.resolve_list_uuid(list_ref)
        await self._call(lambda: self._bring.batch_update_list(uuid, items, BringItemOperation.ADD))
        return uuid

    async def remove_item(self, name: str, list_ref: str | None) -> str:
        uuid = await self.resolve_list_uuid(list_ref)
        await self._call(lambda: self._bring.remove_item(uuid, name))
        return uuid

    async def complete_item(self, name: str, list_ref: str | None) -> str:
        uuid = await self.resolve_list_uuid(list_ref)
        await self._call(lambda: self._bring.complete_item(uuid, name))
        return uuid

    async def import_recipe(
        self,
        url: str,
        base_servings: float | None,
        target_servings: float | None,
        list_ref: str | None,
    ) -> tuple[str, BringTemplate, list[BringItem]]:
        uuid = await self.resolve_list_uuid(list_ref)
        template: BringTemplate = await self._call(lambda: self._bring.parse_recipe(url))
        ingredients = template.ingredients or template.items
        base = base_servings if base_servings else template.baseQuantity
        factor = compute_factor(base, target_servings)
        items: list[BringItem] = [
            BringItem(itemId=ing.itemId, spec=scale_quantity(ing.spec or "", factor))
            for ing in ingredients
        ]
        if items:
            await self._call(
                lambda: self._bring.batch_update_list(uuid, items, BringItemOperation.ADD)
            )
        return uuid, template, items


def _match_by_name(lists: list[BringList], ref: str) -> str | None:
    for item in lists:
        if item.name.casefold() == ref.casefold():
            return item.listUuid
    return None
