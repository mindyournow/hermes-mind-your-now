"""myn_habits: habit tracking, streaks, chains, and reminders."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mind_your_now.client import MynApiClient
from mind_your_now.schemas import action_schema
from mind_your_now.tools import register_myn_tool, tool_error, tool_result


HABITS_SCHEMA = action_schema(
    ["streaks", "skip", "chains", "schedule", "reminders"],
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
        "enableReminders": {"type": "boolean"},
        "reminderTime": {
            "type": "string",
            "pattern": "^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$",
        },
    },
)


def execute_habits(client: MynApiClient, **input_data: Any) -> str:
    action = input_data.get("action")

    if action == "streaks":
        habit_id = input_data.get("habitId")
        if not habit_id:
            return tool_error(
                "habitId is required for streaks action. Use the schedule action to see all habits."
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
        params = (
            {"days": input_data["dateRange"]}
            if input_data.get("dateRange") is not None
            else None
        )
        return tool_result(
            client.get("/api/v2/unified-tasks/schedule", params=params)
        )

    if action == "reminders":
        habit_id = input_data.get("habitId")
        if not habit_id:
            return tool_result(client.get("/api/habits/reminders"))
        enable_reminders = input_data.get("enableReminders")
        reminder_time = input_data.get("reminderTime")
        if enable_reminders is None and not reminder_time:
            return tool_result(client.get(f"/api/habits/reminders/{habit_id}"))
        body = {}
        if enable_reminders is not None:
            body["enabled"] = enable_reminders
        if reminder_time:
            body["time"] = reminder_time
        return tool_result(client.put(f"/api/habits/reminders/{habit_id}", body))

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
            "Track habits, streaks, and reminders. Actions: streaks, skip, "
            "chains, schedule, reminders."
        ),
        emoji="🔁",
    )
