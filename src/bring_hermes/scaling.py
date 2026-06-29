"""Quantity scaling helpers.

Bring! stores the amount of an item in its free-text ``specification`` field
(e.g. ``"500 g"``, ``"2"``, ``"1 EL"``). When a recipe is added for a different
number of servings than it was written for, the leading numeric amount in each
quantity string is scaled by a factor. Units and any trailing text are kept
untouched. Strings without a leading number are returned unchanged.
"""

from __future__ import annotations

import re

_MIXED = re.compile(r"^\s*(\d+)\s+(\d+)\s*/\s*(\d+)\s*(.*)$", re.S)
_FRACTION = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*(.*)$", re.S)
_DECIMAL = re.compile(r"^\s*(\d+(?:[.,]\d+)?)(\s*)(.*)$", re.S)


def compute_factor(base_servings: float | None, target_servings: float | None) -> float:
    """Return target/base when both are sensible, otherwise 1.0 (no scaling)."""
    if base_servings and target_servings and base_servings > 0:
        return target_servings / base_servings
    return 1.0


def scale_quantity(spec: str, factor: float) -> str:
    """Scale the leading numeric amount in ``spec`` by ``factor``."""
    if not spec or not spec.strip() or factor == 1.0:
        return spec

    mixed = _MIXED.match(spec)
    if mixed:
        whole, num, den, rest = mixed.groups()
        if int(den) != 0:
            value = (int(whole) + int(num) / int(den)) * factor
            return _join(_format_number(value), rest)

    fraction = _FRACTION.match(spec)
    if fraction:
        num, den, rest = fraction.groups()
        if int(den) != 0:
            value = (int(num) / int(den)) * factor
            return _join(_format_number(value), rest)

    decimal = _DECIMAL.match(spec)
    if decimal:
        number, gap, rest = decimal.groups()
        value = float(number.replace(",", ".")) * factor
        formatted = _format_number(value)
        if rest:
            # Preserve the original spacing between amount and unit ("250g" vs "250 g").
            return f"{formatted}{gap}{rest}"
        return formatted

    return spec


def _format_number(value: float) -> str:
    rounded = round(value, 2)
    if abs(rounded - round(rounded)) < 1e-9:
        return str(int(round(rounded)))
    return f"{rounded:.2f}".rstrip("0").rstrip(".")


def _join(number_text: str, rest: str) -> str:
    rest = rest.strip()
    return f"{number_text} {rest}" if rest else number_text
