"""Shared JSON Schema helpers for action-multiplexed MYN tools."""

from __future__ import annotations

from typing import Any


def action_schema(
    actions: list[str],
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
) -> dict[str, Any]:
    """Build the common object schema used by MYN action tools."""
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": actions},
            **(properties or {}),
        },
        "required": ["action", *(required or [])],
    }
