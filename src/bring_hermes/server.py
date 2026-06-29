"""FastMCP server definition: the tools exposed to MCP clients."""

from __future__ import annotations

import logging

from bring_api import BringItem
from bring_api.exceptions import BringException
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from .bring_client import BringClient
from .config import Config
from .scaling import compute_factor, scale_quantity

_LOGGER = logging.getLogger(__name__)

INSTRUCTIONS = (
    "Tools to manage Bring! shopping lists. Use `add_recipe` to put all "
    "ingredients of a recipe onto a list in one call, passing the amount of "
    "each ingredient in its `quantity` field (e.g. '500 g', '2', '1 EL'); this "
    "is stored as the item's specification in Bring!. When a list is not "
    "specified, the configured default list is used."
)


class RecipeItem(BaseModel):
    """A single ingredient of a recipe."""

    name: str = Field(description="Ingredient/article name as it should appear in Bring!.")
    quantity: str = Field(
        default="",
        description=(
            "Amount including unit, e.g. '500 g', '2', '1 EL'. Stored as the "
            "Bring! item specification. Leave empty for items without an amount."
        ),
    )


def build_mcp(config: Config, client: BringClient) -> FastMCP:
    """Create the FastMCP instance and register all tools against ``client``."""
    mcp = FastMCP(
        name="bring-hermes",
        instructions=INSTRUCTIONS,
        host=config.host,
        port=config.port,
        streamable_http_path=config.mcp_path,
        json_response=config.json_response,
        stateless_http=True,
    )

    @mcp.tool()
    async def list_shopping_lists() -> str:
        """List all Bring! shopping lists on the account, with their UUIDs."""
        try:
            lists = await client.get_lists(refresh=True)
        except BringException as exc:
            raise ValueError(f"Could not load shopping lists: {exc}") from exc
        if not lists:
            return "No shopping lists found on this account."
        return "\n".join(f"- {item.name} (uuid: {item.listUuid})" for item in lists)

    @mcp.tool()
    async def get_list_items(shopping_list: str | None = None) -> str:
        """Show the items currently on a Bring! list (name + quantity).

        `shopping_list` may be a list name or UUID; omit it to use the default list.
        """
        try:
            uuid, items = await client.list_items(shopping_list)
        except (BringException, ValueError) as exc:
            raise ValueError(f"Could not read the shopping list: {exc}") from exc
        return f"Items on list {uuid}:\n{_format_purchases(items)}"

    @mcp.tool()
    async def add_item(
        name: str, quantity: str = "", shopping_list: str | None = None
    ) -> str:
        """Add a single item to a Bring! list.

        `quantity` is the amount incl. unit (e.g. '500 g') and is stored as the
        item specification. `shopping_list` is a name or UUID; omit for default.
        """
        try:
            uuid = await client.add_item(name, quantity, shopping_list)
        except (BringException, ValueError) as exc:
            raise ValueError(f"Could not add item: {exc}") from exc
        suffix = f" ({quantity.strip()})" if quantity.strip() else ""
        return f"Added '{name}'{suffix} to list {uuid}."

    @mcp.tool()
    async def add_recipe(
        items: list[RecipeItem],
        recipe: str | None = None,
        base_servings: float | None = None,
        target_servings: float | None = None,
        shopping_list: str | None = None,
    ) -> str:
        """Add all ingredients of a recipe to a Bring! list in one operation.

        Each ingredient's `quantity` is stored as its specification. If both
        `base_servings` (what the recipe yields) and `target_servings` (what you
        want) are given, numeric quantities are scaled accordingly. `recipe` is
        an optional name for the confirmation message. `shopping_list` is a name
        or UUID; omit for the default list.
        """
        if not items:
            raise ValueError("`items` must contain at least one ingredient")
        factor = compute_factor(base_servings, target_servings)
        bring_items: list[BringItem] = []
        summary_lines: list[str] = []
        for item in items:
            spec = scale_quantity(item.quantity or "", factor)
            bring_items.append(BringItem(itemId=item.name, spec=spec))
            summary_lines.append(f"- {item.name}" + (f" — {spec}" if spec.strip() else ""))
        try:
            uuid = await client.add_items(bring_items, shopping_list)
        except (BringException, ValueError) as exc:
            raise ValueError(f"Could not add recipe: {exc}") from exc

        header = f"Added {len(bring_items)} ingredient(s)"
        if recipe:
            header += f" for '{recipe}'"
        if factor != 1.0 and base_servings and target_servings:
            header += (
                f" (scaled ×{factor:g} from {base_servings:g} "
                f"to {target_servings:g} servings)"
            )
        header += f" to list {uuid}:"
        return header + "\n" + "\n".join(summary_lines)

    @mcp.tool()
    async def import_recipe_from_url(
        url: str,
        base_servings: float | None = None,
        target_servings: float | None = None,
        shopping_list: str | None = None,
    ) -> str:
        """Parse a recipe from a public URL via Bring!'s parser and add its
        ingredients to a list.

        Optionally scale quantities with `base_servings`/`target_servings` (if
        `base_servings` is omitted, the recipe's own yield is used). `shopping_list`
        is a name or UUID; omit for the default list.
        """
        try:
            uuid, template, items = await client.import_recipe(
                url, base_servings, target_servings, shopping_list
            )
        except (BringException, ValueError) as exc:
            raise ValueError(f"Could not import recipe: {exc}") from exc
        if not items:
            return f"No ingredients could be parsed from {url}."
        name = template.name or "recipe"
        lines = [
            f"- {entry['itemId']}"
            + (f" — {entry['spec']}" if entry.get("spec", "").strip() else "")
            for entry in items
        ]
        return (
            f"Imported '{name}' ({len(items)} ingredient(s)) to list {uuid}:\n"
            + "\n".join(lines)
        )

    @mcp.tool()
    async def remove_item(name: str, shopping_list: str | None = None) -> str:
        """Remove an item from a Bring! list entirely.

        `shopping_list` is a name or UUID; omit for the default list.
        """
        try:
            uuid = await client.remove_item(name, shopping_list)
        except (BringException, ValueError) as exc:
            raise ValueError(f"Could not remove item: {exc}") from exc
        return f"Removed '{name}' from list {uuid}."

    @mcp.tool()
    async def complete_item(name: str, shopping_list: str | None = None) -> str:
        """Mark an item as bought (moves it to the 'recently used' list).

        `shopping_list` is a name or UUID; omit for the default list.
        """
        try:
            uuid = await client.complete_item(name, shopping_list)
        except (BringException, ValueError) as exc:
            raise ValueError(f"Could not complete item: {exc}") from exc
        return f"Marked '{name}' as bought on list {uuid}."

    return mcp


def _format_purchases(items) -> str:
    if not items:
        return "(list is empty)"
    lines = []
    for entry in items:
        spec = (entry.specification or "").strip()
        lines.append(f"- {entry.itemId}" + (f" — {spec}" if spec else ""))
    return "\n".join(lines)
