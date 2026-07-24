"""myn_search: unified search across MYN data."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mind_your_now.client import MynApiClient
from mind_your_now.schemas import action_schema
from mind_your_now.tools import register_myn_tool, tool_error, tool_result


SEARCH_SCHEMA = action_schema(
    ["search"],
    {
        "query": {
            "type": "string",
            "minLength": 1,
            "description": "Search query string",
        },
        "types": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["task", "habit", "chore", "event", "project", "note", "memory"],
            },
        },
        "filters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["PENDING", "IN_PROGRESS", "COMPLETED", "ARCHIVED"],
                },
                "priority": {
                    "type": "string",
                    "enum": [
                        "CRITICAL",
                        "OPPORTUNITY_NOW",
                        "OVER_THE_HORIZON",
                        "PARKING_LOT",
                    ],
                },
                "projectId": {"type": "string"},
                "dateFrom": {"type": "string", "format": "date"},
                "dateTo": {"type": "string", "format": "date"},
            },
        },
        "limit": {"type": "number", "default": 20, "maximum": 100},
        "offset": {"type": "number", "default": 0},
    },
    ["query"],
)


def execute_search(client: MynApiClient, **input_data: Any) -> str:
    action = input_data.get("action")
    if action != "search":
        return tool_error(f"Unknown action: {action}")
    query = input_data.get("query")
    if not query:
        return tool_error("query is required for search action")

    params: dict[str, Any] = {"q": query}
    if input_data.get("types"):
        params["types"] = input_data["types"]
    filters = input_data.get("filters")
    if isinstance(filters, dict):
        for key in ("status", "priority", "projectId", "dateFrom", "dateTo"):
            if filters.get(key):
                params[key] = filters[key]
    if input_data.get("limit") is not None:
        params["limit"] = input_data["limit"]
    if input_data.get("offset") is not None:
        params["offset"] = input_data["offset"]

    return tool_result(client.get("/api/v2/search", params=params))


def register_search_tool(
    ctx: Any,
    client: MynApiClient,
    check_fn: Callable[[], bool],
) -> None:
    register_myn_tool(
        ctx,
        name="myn_search",
        schema=SEARCH_SCHEMA,
        handler=lambda **kwargs: execute_search(client, **kwargs),
        check_fn=check_fn,
        description=(
            "Unified search across tasks, events, notes, and memories. Action: search."
        ),
        emoji="🔎",
    )
