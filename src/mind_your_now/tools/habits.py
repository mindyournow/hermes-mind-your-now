"""myn_habits: habit tracking, streaks, chains, and schedules.

The reminders action was removed as 404-by-construction. Restoration is blocked on MIN-932
and cross-references MIN-883.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mind_your_now.client import MynApiClient
from mind_your_now.schemas import action_schema
from mind_your_now.tools import register_myn_tool, tool_error, tool_result


HABITS_SCHEMA = action_schema(
    ["streaks", "skip", "chains", "schedule"],
    {
        "habitId": {"type": "string", "format": "uuid"},
        "includeHistory": {"type": "boolean", "default": False},
        "skipDate": {"type": "string", "format": "date"},
        "skipReason": {"type": "string"},
        "chainId": {"type": "string", "format": "uuid"},
        "dateRange": {
            "type": "number",
            "default": 7,
            "description": "Number of days to look ahead",
        },
        "limit": {"type": "number", "description": "Maximum habits to return (schedule action only)"},
    },
)


def execute_habits(client: MynApiClient, **input_data: Any) -> str:
    action = input_data.get("action")

    if action == "streaks":
        habit_id = input_data.get("habitId")
        if not habit_id:
            return tool_error(
                "habitId is required for streaks action. Use schedule (which lists habits) to find the habitId."
            )
        params = {"includeHistory": "true"} if input_data.get("includeHistory") else None
        return tool_result(
            client.get(f"/api/v2/unified-tasks/{habit_id}/streak", params=params)
        )

    if action == "skip":
        habit_id = input_data.get("habitId")
        if not habit_id:
            return tool_error("habitId is required for skip action")
        body = {}
        if input_data.get("skipDate"):
            body["skipDate"] = input_data["skipDate"]
        if input_data.get("skipReason"):
            body["reason"] = input_data["skipReason"]
        return tool_result(
            client.post(f"/api/v2/unified-tasks/{habit_id}/skip", body)
        )

    if action == "chains":
        chain_id = input_data.get("chainId")
        path = f"/api/habits/chains/{chain_id}/status" if chain_id else "/api/habits/chains"
        return tool_result(client.get(path))

    if action == "schedule":
        from mind_your_now.tools import truncate
        params = {"type": "HABIT"}
        if input_data.get("dateRange") is not None:
            params["days"] = input_data["dateRange"]
        data = client.get("/api/v2/unified-tasks", params=params)

        # Normalize bare array or wrapped response
        if isinstance(data, list):
            habits = data
        elif isinstance(data, dict) and isinstance(data.get("tasks"), list):
            habits = data["tasks"]
        else:
            return tool_result(data)

        # Defensively filter by taskType and apply limit
        habits = [t for t in habits if t.get("taskType") == "HABIT"]
        limit = input_data.get("limit", 50)
        result = {"tasks": habits}
        result = truncate(result, "tasks", int(limit))
        return tool_result(result)

    return tool_error(f"Unknown action: {action}")


def register_habits_tool(
    ctx: Any,
    client: MynApiClient,
    check_fn: Callable[[], bool],
) -> None:
    register_myn_tool(
        ctx,
        name="myn_habits",
        schema=HABITS_SCHEMA,
        handler=lambda **kwargs: execute_habits(client, **kwargs),
        check_fn=check_fn,
        description=(
            "Track habits and streaks. Actions: streaks, skip, chains, schedule. "
            "Reminders are not supported (see MIN-932 and MIN-883)."
        ),
        emoji="🔁",
    )
