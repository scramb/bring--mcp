"""Lifecycle wrapper around ``bring-api``.

Owns the aiohttp session and the :class:`Bring` instance, handles login plus
re-login on token expiry, and resolves a list reference (UUID or name) to a
list UUID. A single instance is shared across all requests (the underlying
library refreshes the access token automatically inside its request layer).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable
from typing import Callable, TypeVar

import aiohttp
from bring_api import (
    Bring,
    BringItem,
    BringItemOperation,
    BringList,
    BringPurchase,
    BringTemplate,
)
from bring_api.exceptions import BringAuthException, BringException

from .config import Config
from .scaling import compute_factor, scale_quantity

_LOGGER = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

T = TypeVar("T")


class BringClient:
    """Thin, login-aware facade over the Bring! API."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._session: aiohttp.ClientSession | None = None
        self._bring: Bring | None = None
        self._login_lock = asyncio.Lock()
        self._logged_in = False
        self._lists: list[BringList] = []

    @property
    def logged_in(self) -> bool:
        return self._logged_in

    @property
    def _client(self) -> Bring:
        if self._bring is None:
            raise RuntimeError("BringClient has not been started")
        return self._bring

    # ---- lifecycle ----------------------------------------------------
    async def start(self) -> None:
        self._session = aiohttp.ClientSession()
        self._bring = Bring(
            self._session, self._config.bring_email, self._config.bring_password
        )
        try:
            await self._login(force=True)
        except BringException as exc:
            # Don't crash startup if Bring! is briefly unreachable; tools and
            # the readiness probe will retry the login on demand.
            _LOGGER.warning("Initial Bring! login failed, will retry on demand: %s", exc)

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
        self._session = None
        self._bring = None
        self._logged_in = False

    async def ensure_ready(self) -> bool:
        """Used by the readiness probe: True once authenticated with Bring!."""
        try:
            await self._login(force=False)
            return True
        except BringException:
            return False

    async def _login(self, *, force: bool) -> None:
        if self._logged_in and not force:
            return
        async with self._login_lock:
            if self._logged_in and not force:
                return
            await self._client.login()
            self._logged_in = True
            _LOGGER.info("Authenticated with Bring! as %s", self._config.bring_email)

    async def _call(self, factory: Callable[[], Awaitable[T]]) -> T:
        """Run an API call, re-authenticating once on an auth failure."""
        await self._login(force=False)
        try:
            return await factory()
        except BringAuthException:
            _LOGGER.info("Bring! authorization expired, re-authenticating")
            await self._login(force=True)
            return await factory()

    # ---- list resolution ----------------------------------------------
    async def get_lists(self, *, refresh: bool = False) -> list[BringList]:
        if self._lists and not refresh:
            return self._lists
        response = await self._call(lambda: self._client.load_lists())
        self._lists = list(response.lists)
        return self._lists

    async def resolve_list_uuid(self, list_ref: str | None) -> str:
        ref = (list_ref or self._config.default_list or "").strip()
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
            raise ValueError(
                f"Shopping list {ref!r} not found. Available lists: {available}"
            )
        return match

    # ---- operations ---------------------------------------------------
    async def list_items(self, list_ref: str | None) -> tuple[str, list[BringPurchase]]:
        uuid = await self.resolve_list_uuid(list_ref)
        response = await self._call(lambda: self._client.get_list(uuid))
        return uuid, list(response.items.purchase)

    async def add_item(self, name: str, quantity: str, list_ref: str | None) -> str:
        uuid = await self.resolve_list_uuid(list_ref)
        await self._call(lambda: self._client.save_item(uuid, name, quantity or ""))
        return uuid

    async def add_items(self, items: list[BringItem], list_ref: str | None) -> str:
        uuid = await self.resolve_list_uuid(list_ref)
        await self._call(
            lambda: self._client.batch_update_list(uuid, items, BringItemOperation.ADD)
        )
        return uuid

    async def remove_item(self, name: str, list_ref: str | None) -> str:
        uuid = await self.resolve_list_uuid(list_ref)
        await self._call(lambda: self._client.remove_item(uuid, name))
        return uuid

    async def complete_item(self, name: str, list_ref: str | None) -> str:
        uuid = await self.resolve_list_uuid(list_ref)
        await self._call(lambda: self._client.complete_item(uuid, name))
        return uuid

    async def import_recipe(
        self,
        url: str,
        base_servings: float | None,
        target_servings: float | None,
        list_ref: str | None,
    ) -> tuple[str, BringTemplate, list[BringItem]]:
        uuid = await self.resolve_list_uuid(list_ref)
        template: BringTemplate = await self._call(lambda: self._client.parse_recipe(url))
        ingredients = template.ingredients or template.items
        base = base_servings if base_servings else template.baseQuantity
        factor = compute_factor(base, target_servings)
        items: list[BringItem] = [
            BringItem(itemId=ing.itemId, spec=scale_quantity(ing.spec or "", factor))
            for ing in ingredients
        ]
        if items:
            await self._call(
                lambda: self._client.batch_update_list(uuid, items, BringItemOperation.ADD)
            )
        return uuid, template, items


def _match_by_name(lists: list[BringList], ref: str) -> str | None:
    for item in lists:
        if item.name.casefold() == ref.casefold():
            return item.listUuid
    return None
