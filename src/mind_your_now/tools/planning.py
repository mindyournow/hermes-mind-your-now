"""myn_planning: AI planning and scheduling."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mind_your_now.client import MynApiClient
from mind_your_now.schemas import action_schema
from mind_your_now.tools import register_myn_tool, tool_error, tool_result


PLANNING_SCHEMA = action_schema(
    ["plan", "schedule_all", "reschedule"],
    {
        "goal": {"type": "string", "description": "What you want to accomplish"},
        "constraints": {
            "type": "object",
            "properties": {
                "availableHours": {"type": "number"},
                "preferredTimes": {"type": "array", "items": {"type": "string"}},
                "avoidTimes": {"type": "array", "items": {"type": "string"}},
                "deadline": {"type": "string", "format": "date-time"},
                "priority": {
                    "type": "string",
                    "enum": ["CRITICAL", "OPPORTUNITY_NOW", "OVER_THE_HORIZON"],
                },
            },
        },
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "estimatedDuration": {"type": "number"},
                    "dependsOn": {"type": "array", "items": {"type": "string"}},
                    "fixedTime": {"type": "string", "format": "date-time"},
                },
                "required": ["title"],
            },
        },
        "date": {"type": "string", "format": "date"},
        "respectExisting": {"type": "boolean", "default": True},
        "bufferMinutes": {"type": "number", "default": 15},
        "taskIds": {
            "type": "array",
            "items": {"type": "string", "format": "uuid"},
        },
        "reason": {"type": "string"},
        "targetDate": {"type": "string", "format": "date"},
        "spreadOverDays": {"type": "number", "default": 1},
    },
)


def execute_planning(client: MynApiClient, **input_data: Any) -> str:
    action = input_data.get("action")
    if action == "plan":
        return tool_result({"result": client.get("/planning/plan")})
    if action == "schedule_all":
        return tool_result({"result": client.post("/planning/scheduleAll", {})})
    if action == "reschedule":
        rebalance = "true" if (input_data.get("spreadOverDays") or 0) > 1 else "false"
        return tool_result(
            client.post(
                "/planning/kickTheCan",
                {},
                params={"rebalance": rebalance},
            )
        )
    return tool_error(f"Unknown action: {action}")


def register_planning_tool(
    ctx: Any,
    client: MynApiClient,
    check_fn: Callable[[], bool],
) -> None:
    register_myn_tool(
        ctx,
        name="myn_planning",
        schema=PLANNING_SCHEMA,
        handler=lambda **kwargs: execute_planning(client, **kwargs),
        check_fn=check_fn,
        description=(
            "AI-powered planning and scheduling. Actions: plan, schedule_all, "
            "reschedule."
        ),
        emoji="🗓️",
    )
