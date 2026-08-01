"""myn_memory: remember, recall, forget, and server-side search."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mind_your_now.client import MynApiClient
from mind_your_now.schemas import action_schema
from mind_your_now.tools import register_myn_tool, tool_error, tool_result


MEMORY_FETCH_LIMIT = 50
MEMORY_SCHEMA = action_schema(
    ["remember", "recall", "forget", "search"],
    {
        "content": {
            "type": "string",
            "minLength": 1,
            "description": "Memory content to store (max 500 chars)",
        },
        "category": {
            "type": "string",
            "enum": [
                "PREFERENCE",
                "PATTERN",
                "STYLE",
                "MYN_BEHAVIOR",
                "PERSONAL",
                "RELATIONSHIP",
            ],
        },
        "memoryId": {"type": "string", "format": "uuid"},
        "query": {"type": "string"},
        "limit": {"type": "number", "default": 10},
    },
)


def execute_memory(client: MynApiClient, **input_data: Any) -> str:
    action = input_data.get("action")

    if action == "remember":
        if not input_data.get("content"):
            return tool_error("content is required for remember action")
        body = {"content": input_data["content"]}
        if input_data.get("category"):
            body["type"] = input_data["category"]
        return tool_result(client.post("/api/v1/agent/memories", body))

    if action == "recall":
        limit = input_data.get("limit")
        data = client.get(
            "/api/v1/customers/memories",
            params={"limit": MEMORY_FETCH_LIMIT if limit is None else limit},
        )
        memories = data.get("memories", []) if isinstance(data, dict) else []
        memory_id = input_data.get("memoryId")
        if memory_id:
            match = next(
                (
                    memory
                    for memory in memories
                    if memory.get("id") == memory_id
                ),
                None,
            )
            if not match:
                return tool_error(f"Memory not found: {memory_id}")
            return tool_result(match)
        return tool_result(memories)

    if action == "forget":
        memory_id = input_data.get("memoryId")
        if not memory_id:
            return tool_error("memoryId is required for forget action")
        client.delete(f"/api/v1/customers/memories/{memory_id}")
        return tool_result({"deleted": True, "memoryId": memory_id})

    if action == "search":
        query = input_data.get("query")
        if not query:
            return tool_error("query is required for search action")
        limit = input_data.get("limit")
        return tool_result(
            client.get(
                "/api/v1/agent/memories/context",
                params={"query": query, "limit": 10 if limit is None else limit},
            )
        )

    return tool_error(f"Unknown action: {action}")


def register_memory_tool(
    ctx: Any,
    client: MynApiClient,
    check_fn: Callable[[], bool],
) -> None:
    register_myn_tool(
        ctx,
        name="myn_memory",
        schema=MEMORY_SCHEMA,
        handler=lambda **kwargs: execute_memory(client, **kwargs),
        check_fn=check_fn,
        description=(
            "Store and retrieve agent memories. Actions: remember, recall, "
            "forget, search."
        ),
        emoji="🧠",
    )
