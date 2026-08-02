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
MEMORY_LOOKUP_PAGE_SIZE = 200
MEMORY_LOOKUP_MAX_PAGES = 50


def _memory_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict) and isinstance(data.get("memories"), list):
        return data["memories"]
    if isinstance(data, list):
        return data
    return []


def _find_memory_by_id(
    client: MynApiClient,
    memory_id: str,
) -> tuple[dict[str, Any] | None, bool]:
    offset = 0
    for _page in range(MEMORY_LOOKUP_MAX_PAGES):
        data = client.get(
            "/api/v1/customers/memories",
            params={"limit": MEMORY_LOOKUP_PAGE_SIZE, "offset": offset},
        )
        memories = _memory_items(data)
        match = next(
            (
                memory
                for memory in memories
                if (memory.get("id") or memory.get("memoryId")) == memory_id
            ),
            None,
        )
        if match is not None:
            return match, True

        has_more = bool(data.get("hasMore")) if isinstance(data, dict) else False
        if not has_more:
            return None, True
        if not memories:
            return None, False
        offset += len(memories)

    return None, False


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
        memory_id = input_data.get("memoryId")
        if memory_id:
            match, complete = _find_memory_by_id(client, memory_id)
            if match is not None:
                return tool_result(match)
            if not complete:
                return tool_error(
                    "Memory lookup reached its 50-page safety cap before completion"
                )
            return tool_error(f"Memory not found: {memory_id}")

        limit = input_data.get("limit")
        data = client.get(
            "/api/v1/customers/memories",
            params={"limit": MEMORY_FETCH_LIMIT if limit is None else limit},
        )
        memories = _memory_items(data)

        # Preserve main's transparent client-side truncation markers.
        from mind_your_now.tools import truncate
        result = data if isinstance(data, dict) else {"memories": memories}
        if "memories" not in result:
            result = {"memories": memories}
        if limit:
            result = truncate(result, "memories", int(limit))

        return tool_result(result)

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
