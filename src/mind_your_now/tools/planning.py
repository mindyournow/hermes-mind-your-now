"""myn_planning: AI planning and scheduling.

⚠️  WARNING: plan, schedule_all, and reschedule are user-wide mutations despite
read-shaped API surfaces. The model MUST ask the user for permission before a live call.

schedule_all and reschedule support a read-only dryRun that returns the candidate task set.
The planning engine's resulting dates and placements cannot be previewed until MIN-932.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mind_your_now.client import MynApiClient
from mind_your_now.schemas import action_schema
from mind_your_now.tools import (
    UnifiedTaskScan,
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
        "previewLimit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 200,
            "default": 50,
            "description": "Maximum candidate tasks returned by dryRun; count remains the full candidate count.",
        },
    },
)


def _planning_context(client: MynApiClient) -> tuple[str, ZoneInfo]:
    data = client.get("/api/v1/customers/planning-context")
    if not isinstance(data, dict):
        raise RuntimeError("Planning context returned an unexpected response shape")

    customer_id = data.get("id")
    zone_name = data.get("defaultTimeZone")
    if customer_id is None:
        raise RuntimeError("Planning context does not contain a customer ID")
    if not zone_name:
        raise RuntimeError("Planning context does not contain defaultTimeZone")

    try:
        zone = ZoneInfo(str(zone_name))
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(f"Unknown customer timezone: {zone_name}") from exc
    return str(customer_id), zone


def _now_in_zone(zone: ZoneInfo) -> datetime:
    return datetime.now(zone)


def _task_start(task: dict[str, Any], zone: ZoneInfo) -> datetime | None:
    value = task.get("startDate")
    if value is None:
        return None
    text = str(value)
    try:
        if len(text) == 10:
            return datetime.combine(date.fromisoformat(text), time.min, tzinfo=zone)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(zone)


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


def _schedule_all_candidates(
    tasks: list[dict[str, Any]],
    *,
    zone: ZoneInfo,
    now: datetime,
) -> list[dict[str, Any]]:
    end_of_today = datetime.combine(
        now.date() + timedelta(days=1),
        time.min,
        tzinfo=zone,
    )
    eligible = [
        task
        for task in tasks
        if (task_start := _task_start(task, zone)) is not None
        and task_start <= end_of_today
        and task.get("priority") not in {"OVER_THE_HORIZON", "PARKING_LOT"}
        and not _is_completed(task)
    ]
    opportunity = sorted(
        (task for task in eligible if task.get("priority") == "OPPORTUNITY_NOW"),
        key=lambda task: _task_start(task, zone) or end_of_today,
    )[:10]
    other = [task for task in eligible if task.get("priority") != "OPPORTUNITY_NOW"]
    return sorted(
        [*other, *opportunity],
        key=lambda task: _task_start(task, zone) or end_of_today,
    )


def _reschedule_candidates(
    tasks: list[dict[str, Any]],
    *,
    rebalance: bool,
    zone: ZoneInfo,
    now: datetime,
) -> list[dict[str, Any]]:
    latest = datetime.max.replace(tzinfo=zone)
    return sorted(
        (
            task
            for task in tasks
            if not _is_completed(task)
            and not bool(task.get("isAutoScheduled"))
            and not _is_recurring(task)
            and (
                rebalance
                or ((_task_start(task, zone) or latest).date() <= now.date())
            )
        ),
        key=lambda task: _task_start(task, zone) or latest,
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
    preview_limit: int,
    rebalance: bool = False,
) -> str:
    customer_id, zone = _planning_context(client)
    now = _now_in_zone(zone)
    data = fetch_all_unified_tasks(client, params={"detail": "full"})
    if not isinstance(data, UnifiedTaskScan):
        return tool_error("Unable to read the task collection for planning dryRun")

    owned_tasks = [
        task
        for task in data.tasks
        if task.get("ownerId") is not None
        and str(task["ownerId"]) == customer_id
    ]
    if action == "schedule_all":
        candidates = _schedule_all_candidates(owned_tasks, zone=zone, now=now)
    else:
        candidates = _reschedule_candidates(
            owned_tasks,
            rebalance=rebalance,
            zone=zone,
            now=now,
        )

    candidate_count = len(candidates)
    tasks = [
        _slim_preview_task(task)
        for task in candidates[:preview_limit]
    ]
    payload = {
        "dryRun": True,
        "action": action,
        "tasks": tasks,
        "count": candidate_count,
        "customerTimeZone": str(zone),
        "engineDecisionsPreviewed": False,
        "collectionComplete": data.complete,
        "scannedTaskCount": data.scanned_items,
        "message": (
            "Read-only candidate task preview in the customer's configured timezone. "
            "Only tasks owned by the customer are included, matching live planning. "
            "The planning engine's resulting dates and placements cannot be previewed. "
            "Multi-page reads are deduplicated, snapshot-bound, and capped at "
            "50 pages or 10,000 tasks."
        ),
    }
    if not data.complete:
        payload["_truncated"] = True
        payload["countIsLowerBound"] = True
    elif len(tasks) < candidate_count:
        payload["_truncated"] = True
        payload["_totalCount"] = candidate_count
    return tool_result(payload)


def execute_planning(client: MynApiClient, **input_data: Any) -> str:
    action = input_data.get("action")
    preview_limit = input_data.get("previewLimit", 50)
    if input_data.get("dryRun"):
        if action == "plan":
            return tool_error("dryRun is supported only for schedule_all and reschedule")
        if type(preview_limit) is not int or not 1 <= preview_limit <= 200:
            return tool_error("previewLimit must be an integer between 1 and 200")

    if action == "plan":
        return tool_result({"result": client.get("/planning/plan")})
    if action == "schedule_all":
        if input_data.get("dryRun"):
            return _dry_run_preview(
                client,
                action=action,
                preview_limit=preview_limit,
            )
        return tool_result({"result": client.post("/planning/scheduleAll", {})})
    if action == "reschedule":
        rebalance = (input_data.get("spreadOverDays") or 0) > 1
        if input_data.get("dryRun"):
            return _dry_run_preview(
                client,
                action=action,
                preview_limit=preview_limit,
                rebalance=rebalance,
            )
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
