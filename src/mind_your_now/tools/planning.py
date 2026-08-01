"""myn_planning: AI planning and scheduling.

⚠️  WARNING: plan, schedule_all, and reschedule are user-wide mutations despite
read-shaped API surfaces. The model MUST ask the user for permission before a live call.

schedule_all and reschedule support a read-only dryRun that returns the candidate task set.
The planning engine's resulting dates and placements cannot be previewed until MIN-932.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from mind_your_now.client import MynApiClient
from mind_your_now.schemas import action_schema
from mind_your_now.tools import (
    fetch_all_unified_tasks,
    register_myn_tool,
    tool_error,
    tool_result,
)


PLANNING_SCHEMA = action_schema(
    ["plan", "schedule_all", "reschedule"],
    {
        "spreadOverDays": {
            "type": "number",
            "default": 1,
            "description": "Number of days to spread scheduling over (reschedule only)",
        },
        "dryRun": {
            "type": "boolean",
            "description": (
                "Return the user-wide candidate task set without invoking the planning "
                "engine (schedule_all and reschedule only). Engine decisions are not previewed."
            ),
        },
    },
)


def _task_date(task: dict[str, Any]) -> date | None:
    value = task.get("startDate")
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _is_completed(task: dict[str, Any]) -> bool:
    return bool(task.get("isCompleted")) or task.get("status") in {
        "COMPLETED",
        "ARCHIVED",
    }


def _is_recurring(task: dict[str, Any]) -> bool:
    recurrence_rule = str(task.get("recurrenceRule") or "")
    task_type = str(task.get("taskType") or "").upper()
    return (
        bool(recurrence_rule and recurrence_rule.lower() != "once")
        or bool(task.get("parentTaskId") or task.get("parentTask"))
        or task_type in {"HABIT", "CHORE", "RECURRING_TASK", "RECURRINGTASK"}
    )


def _schedule_all_candidates(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    today = date.today()
    eligible = [
        task
        for task in tasks
        if (task_date := _task_date(task)) is not None
        and task_date <= today
        and task.get("priority") not in {"OVER_THE_HORIZON", "PARKING_LOT"}
        and not _is_completed(task)
    ]
    opportunity = sorted(
        (task for task in eligible if task.get("priority") == "OPPORTUNITY_NOW"),
        key=lambda task: _task_date(task) or today,
    )[:10]
    other = [task for task in eligible if task.get("priority") != "OPPORTUNITY_NOW"]
    return sorted([*other, *opportunity], key=lambda task: _task_date(task) or today)


def _reschedule_candidates(
    tasks: list[dict[str, Any]],
    *,
    rebalance: bool,
) -> list[dict[str, Any]]:
    today = date.today()
    return sorted(
        (
            task
            for task in tasks
            if not _is_completed(task)
            and not bool(task.get("isAutoScheduled"))
            and not _is_recurring(task)
            and (rebalance or ((_task_date(task) or date.max) <= today))
        ),
        key=lambda task: _task_date(task) or date.max,
    )


def _slim_preview_task(task: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "id",
        "title",
        "taskType",
        "priority",
        "status",
        "isCompleted",
        "isAutoScheduled",
        "startDate",
        "endDate",
        "recurrenceRule",
        "parentTaskId",
    )
    return {field: task[field] for field in fields if field in task}


def _dry_run_preview(
    client: MynApiClient,
    *,
    action: str,
    rebalance: bool = False,
) -> str:
    data = fetch_all_unified_tasks(client)
    if not isinstance(data, list):
        return tool_error("Unable to read the task collection for planning dryRun")

    if action == "schedule_all":
        candidates = _schedule_all_candidates(data)
    else:
        candidates = _reschedule_candidates(data, rebalance=rebalance)

    tasks = [_slim_preview_task(task) for task in candidates]
    return tool_result(
        {
            "dryRun": True,
            "action": action,
            "tasks": tasks,
            "count": len(tasks),
            "engineDecisionsPreviewed": False,
            "message": (
                "Read-only candidate task preview. The planning engine's resulting dates "
                "and placements cannot be previewed until MIN-932."
            ),
        }
    )


def execute_planning(client: MynApiClient, **input_data: Any) -> str:
    action = input_data.get("action")
    if action == "plan":
        return tool_result({"result": client.get("/planning/plan")})
    if action == "schedule_all":
        if input_data.get("dryRun"):
            return _dry_run_preview(client, action=action)
        return tool_result({"result": client.post("/planning/scheduleAll", {})})
    if action == "reschedule":
        rebalance = (input_data.get("spreadOverDays") or 0) > 1
        if input_data.get("dryRun"):
            return _dry_run_preview(client, action=action, rebalance=rebalance)
        return tool_result(
            client.post(
                "/planning/kickTheCan",
                {},
                params={"rebalance": "true" if rebalance else "false"},
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
            "⚠️ LIVE ACTIONS ARE USER-WIDE AND MUTATE SCHEDULING STATE. "
            "schedule_all and reschedule support dryRun candidate previews that do not "
            "invoke the planning engine. ASK THE USER FOR PERMISSION BEFORE LIVE CALLS."
        ),
        emoji="🗓️",
    )
