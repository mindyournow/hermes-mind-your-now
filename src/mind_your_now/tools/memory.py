"""myn_memory: remember, recall, forget, and semantic-recall search.

The search action has been renamed to recall_relevant and returns semantic
matches, not exact matches. The search action remains callable as a deprecated
alias for one release.

Filtered search (exact keyword match) is blocked by MIN-932 and will be added
in a follow-up when the search endpoint is implemented server-side.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mind_your_now.client import MynApiClient
from mind_your_now.schemas import action_schema
from mind_your_now.tools import register_myn_tool, tool_error, tool_result


MEMORY_FETCH_LIMIT = 50
MEMORY_SCHEMA = action_schema(
    ["remember", "recall", "forget", "recall_relevant", "search"],
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
        "query": {"type": "string", "description": "Search query for semantic recall (returns semantic matches, not exact matches)"},
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
        data = client.get(
            "/api/v1/customers/memories",
            params={"limit": MEMORY_FETCH_LIMIT},
        )
        memory_id = input_data.get("memoryId")
        if memory_id:
            # Handle both wrapped response {"memories": [...], "totalCount": N} and bare list
            if isinstance(data, dict) and "memories" in data:
                memories = data["memories"]
            elif isinstance(data, list):
                memories = data
            else:
                memories = []
            match = next(
                (
                    memory
                    for memory in memories
                    if (memory.get("id") or memory.get("memoryId")) == memory_id
                ),
                None,
            )
            if not match:
                return tool_error(f"Memory not found: {memory_id}")
            return tool_result(match)

        # Apply client-side limit to recall (when not searching by id)
        from mind_your_now.tools import truncate
        limit = input_data.get("limit")
        if limit:
            # Normalize data to have "memories" key if wrapped differently
            if isinstance(data, dict) and "memories" not in data:
                # Assume data is the raw response, wrap it
                data = {"memories": data.get("items", data.get("results", []))}
            elif isinstance(data, list):
                data = {"memories": data}
            data = truncate(data, "memories", int(limit))

        return tool_result(data)

    if action == "forget":
        memory_id = input_data.get("memoryId")
        if not memory_id:
            return tool_error("memoryId is required for forget action")
        client.delete(f"/api/v1/customers/memories/{memory_id}")
        return tool_result({"deleted": True, "memoryId": memory_id})

    if action in {"recall_relevant", "search"}:
        query = input_data.get("query")
        if not query:
            action_name = "recall_relevant" if action == "recall_relevant" else "search (deprecated)"
            return tool_error(f"query is required for {action_name} action")
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
            "forget, recall_relevant (semantic search, replaces search). "
            "search remains as a deprecated alias for one release."
        ),
        emoji="🧠",
    )
