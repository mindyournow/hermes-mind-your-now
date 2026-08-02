"""myn_habits: habit tracking, streaks, chains, schedules, and reminders."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mind_your_now.client import MynApiClient
from mind_your_now.schemas import action_schema
from mind_your_now.tools import (
    fetch_all_unified_tasks,
    register_myn_tool,
    tool_error,
    tool_result,
    truncate,
)


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
        "limit": {
            "type": "integer",
            "minimum": 1,
            "description": "Maximum habits to return (schedule action only)",
        },
        "enableReminders": {
            "type": "boolean",
            "description": "Set reminderEnabled on the habit's unified task entity.",
        },
        "reminderTime": {
            "type": "string",
            "pattern": "^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$",
            "description": "Set reminderTime on the habit's unified task entity.",
        },
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
        limit = input_data.get("limit", 50)
        if type(limit) is not int or limit < 1:
            return tool_error("limit must be a positive integer")

        params = {"type": "HABIT"}
        if input_data.get("dateRange") is not None:
            params["days"] = input_data["dateRange"]
        data = fetch_all_unified_tasks(client, params=params)
        if not isinstance(data, list):
            return tool_result(data)

        # Defensively filter because older servers may ignore the type parameter.
        habits = [task for task in data if task.get("taskType") == "HABIT"]
        return tool_result(truncate({"tasks": habits}, "tasks", limit))

    if action == "reminders":
        habit_id = input_data.get("habitId")
        if habit_id:
            enable_reminders = input_data.get("enableReminders")
            reminder_time = input_data.get("reminderTime")
            if enable_reminders is not None or reminder_time is not None:
                updates = {}
                if enable_reminders is not None:
                    updates["reminderEnabled"] = enable_reminders
                if reminder_time is not None:
                    updates["reminderTime"] = reminder_time
                return tool_result(
                    client.guarded_write(
                        "PATCH",
                        f"/api/v2/unified-tasks/{habit_id}",
                        json=updates,
                        get_path=f"/api/v2/unified-tasks/{habit_id}",
                    )
                )

            task = client.get(f"/api/v2/unified-tasks/{habit_id}")
            return tool_result(
                {
                    "habitId": habit_id,
                    "reminderEnabled": bool(task.get("reminderEnabled")),
                    "reminderTime": task.get("reminderTime"),
                }
            )

        data = fetch_all_unified_tasks(client, params={"type": "HABIT"})
        habits = data.get("tasks", []) if isinstance(data, dict) else data
        reminders = [
            {
                "habitId": task["id"],
                "title": task.get("title"),
                "reminderTime": task.get("reminderTime"),
            }
            for task in habits
            if task.get("taskType") == "HABIT" and task.get("reminderEnabled")
        ]
        return tool_result({"reminders": reminders})

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
            "Track habits, streaks, and reminders. Actions: streaks, skip, chains, schedule, reminders. "
            "Reminder settings live on the habit's unified task entity."
        ),
        emoji="🔁",
    )
