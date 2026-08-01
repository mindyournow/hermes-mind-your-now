"""myn_planning: AI planning and scheduling.

⚠️  WARNING: All three planning actions (plan, schedule_all, reschedule) mutate scheduling
state despite their read-shaped API surfaces. They are user-wide, not scoped to a single
task or date. The model MUST ask the user for permission before calling any planning action.

dryRun returns the affected task set but cannot preview the engine's scheduling decisions.
Engine decision preview is blocked by MIN-932.

See MIN-932 for scoped planning support and decision preview.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mind_your_now.client import MynApiClient
from mind_your_now.schemas import action_schema
from mind_your_now.tools import register_myn_tool, tool_error, tool_result


PLANNING_SCHEMA = action_schema(
    ["plan", "schedule_all", "reschedule"],
    {
        "spreadOverDays": {"type": "number", "default": 1, "description": "Number of days to spread scheduling over (reschedule only)"},
        "dryRun": {"type": "boolean", "description": "Preview the scheduling changes without applying them (schedule_all, reschedule only)"},
    },
)


def execute_planning(client: MynApiClient, **input_data: Any) -> str:
    action = input_data.get("action")
    if action == "plan":
        return tool_result({"result": client.get("/planning/plan")})
    if action == "schedule_all":
        if input_data.get("dryRun"):
            # Dry run: fetch what would be scheduled but don't apply
            result = client.post("/planning/scheduleAll", {})
            return tool_result({
                "dryRun": True,
                "tasks": result.get("tasks", []) if isinstance(result, dict) else [],
                "count": len(result.get("tasks", [])) if isinstance(result, dict) else 0,
                "message": "Engine decisions cannot be previewed — only the affected task set is shown. See MIN-932 for scope."
            })
        return tool_result({"result": client.post("/planning/scheduleAll", {})})
    if action == "reschedule":
        rebalance = "true" if (input_data.get("spreadOverDays") or 0) > 1 else "false"
        if input_data.get("dryRun"):
            # Dry run: fetch what would be rescheduled but don't apply
            result = client.post(
                "/planning/kickTheCan",
                {},
                params={"rebalance": rebalance},
            )
            return tool_result({
                "dryRun": True,
                "tasks": result.get("tasks", []) if isinstance(result, dict) else [],
                "count": len(result.get("tasks", [])) if isinstance(result, dict) else 0,
                "message": "Engine decisions cannot be previewed — only the affected task set is shown. See MIN-932 for scope."
            })
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
            "AI-powered planning and scheduling. Actions: plan, schedule_all, reschedule. "
            "⚠️ ALL ACTIONS ARE USER-WIDE AND MUTATE SCHEDULING STATE. "
            "ASK THE USER FOR PERMISSION BEFORE CALLING."
        ),
        emoji="🗓️",
    )
