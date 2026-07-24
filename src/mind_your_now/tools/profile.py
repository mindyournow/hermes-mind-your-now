"""myn_profile: user info, goals, and preferences."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from mind_your_now.client import MynApiClient
from mind_your_now.schemas import action_schema
from mind_your_now.tools import register_myn_tool, tool_error, tool_result


logger = logging.getLogger(__name__)
PREFERENCE_ENDPOINTS = {
    "notification-preferences": "/api/v1/customers/notification-preferences",
    "coaching-intensity": "/api/v1/customers/coaching-intensity",
    "theme-preference": "/api/v1/customers/theme-preference",
}

PROFILE_SCHEMA = action_schema(
    ["get_info", "get_goals", "update_goals", "preferences"],
    {
        "goals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "targetDate": {"type": "string", "format": "date"},
                    "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                    "status": {
                        "type": "string",
                        "enum": ["active", "completed", "paused", "abandoned"],
                    },
                },
                "required": ["title"],
            },
        },
        "goalId": {"type": "string"},
        "preferenceKey": {"type": "string"},
        "preferenceValue": {},
        "preferenceCategory": {
            "type": "string",
            "enum": ["notifications", "display", "ai", "privacy", "integrations"],
        },
    },
)


def _escape_markdown(text: str) -> str:
    escaped = text.replace("\\", "\\\\")
    return re.sub(r"([*_~`\[\]()#+\-!|])", r"\\\1", escaped)


def execute_profile(client: MynApiClient, **input_data: Any) -> str:
    action = input_data.get("action")

    if action == "get_info":
        return tool_result(client.get("/api/v1/customers"))

    if action == "get_goals":
        return tool_result(client.get("/api/v1/customers/goals"))

    if action == "update_goals":
        goals = input_data.get("goals")
        if not goals:
            return tool_error("goals array is required for update_goals action")
        lines = []
        for goal in goals:
            line = f"- **{_escape_markdown(goal['title'])}**"
            if goal.get("status"):
                line += f" [{_escape_markdown(str(goal['status']))}]"
            if goal.get("priority"):
                line += f" ({_escape_markdown(str(goal['priority']))} priority)"
            if goal.get("description"):
                line += f"\n  {_escape_markdown(goal['description'])}"
            if goal.get("targetDate"):
                line += f"\n  Target: {_escape_markdown(str(goal['targetDate']))}"
            lines.append(line)
        return tool_result(
            client.put(
                "/api/v1/customers/goals",
                {"goalsAndAmbitions": "\n".join(lines)},
            )
        )

    if action == "preferences":
        if "preferenceKey" in input_data:
            key = input_data["preferenceKey"]
            endpoint = PREFERENCE_ENDPOINTS.get(key)
            if not endpoint:
                return tool_error(
                    f"Unknown preferenceKey: {key}. Valid keys: "
                    f"{', '.join(PREFERENCE_ENDPOINTS)}"
                )
            if "preferenceValue" in input_data:
                return tool_result(client.put(endpoint, input_data["preferenceValue"]))
            return tool_result(client.get(endpoint))

        preferences = {}
        for key, endpoint in PREFERENCE_ENDPOINTS.items():
            try:
                preferences[key] = client.get(endpoint)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[myn_profile] Failed to load %s: %s", key, exc)
                preferences[key] = None
        return tool_result({"preferences": preferences})

    return tool_error(f"Unknown action: {action}")


def register_profile_tool(
    ctx: Any,
    client: MynApiClient,
    check_fn: Callable[[], bool],
) -> None:
    register_myn_tool(
        ctx,
        name="myn_profile",
        schema=PROFILE_SCHEMA,
        handler=lambda **kwargs: execute_profile(client, **kwargs),
        check_fn=check_fn,
        description=(
            "Manage user profile, goals, and preferences. Actions: get_info, "
            "get_goals, update_goals, preferences."
        ),
        emoji="👤",
    )
