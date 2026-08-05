"""myn_tasks: task CRUD, lifecycle, and search."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import date
from typing import Any

from mind_your_now.client import MynApiClient
from mind_your_now.schemas import action_schema
from mind_your_now.tools import (
    UnifiedTaskScan,
    fetch_all_unified_tasks,
    register_myn_tool,
    tool_error,
    tool_result,
    truncate,
)


logger = logging.getLogger(__name__)

PRIORITIES = [
    "CRITICAL",
    "OPPORTUNITY_NOW",
    "OVER_THE_HORIZON",
    "PARKING_LOT",
]
TASK_TYPES = ["TASK", "HABIT", "CHORE"]
TASK_STATUSES = ["PENDING", "IN_PROGRESS", "COMPLETED", "ARCHIVED"]

ALLOWED_UPDATE_FIELDS = {
    "title",
    "description",
    "priority",
    "status",
    "startDate",
    "endDate",
    "duration",
    "projectId",
    "recurrenceRule",
    "isAutoScheduled",
    "autoScheduleEnabled",
    "calendarId",
    "location",
    "notes",
    "tags",
    "estimatedMinutes",
    "actualMinutes",
    "completedAt",
    "archivedAt",
    "taskType",
    "assignedTo",
    "scheduledAt",
    "dueDate",
    "reminderEnabled",
    "reminderTime",
}

TASKS_SCHEMA = action_schema(
    ["list", "get", "create", "update", "complete", "archive", "search"],
    {
        "status": {"type": "string", "enum": TASK_STATUSES},
        "priority": {"type": "string", "enum": PRIORITIES},
        "projectId": {"type": "string"},
        "startDate": {
            "type": "string",
            "format": "date",
            "description": "For list, include tasks whose start/end interval overlaps this date or later. For create, the task start date.",
        },
        "endDate": {
            "type": "string",
            "format": "date",
            "description": "For list, include tasks whose start/end interval overlaps this date or earlier.",
        },
        "limit": {"type": "integer", "minimum": 1, "default": 20},
        "offset": {"type": "integer", "minimum": 0, "default": 0},
        "taskId": {"type": "string", "format": "uuid"},
        "title": {"type": "string", "minLength": 1, "maxLength": 200},
        "description": {"type": "string", "maxLength": 2000},
        "taskType": {"type": "string", "enum": TASK_TYPES},
        "duration": {"type": "string"},
        "id": {"type": "string", "format": "uuid"},
        "recurrenceRule": {"type": "string"},
        "isAutoScheduled": {
            "type": "boolean",
            "description": "Enable auto-scheduling by the planning system. Defaults to true — only set false if user explicitly opts out.",
        },
        "autoScheduleEnabled": {
            "type": "boolean",
            "description": "DEPRECATED alias for isAutoScheduled. Prefer isAutoScheduled.",
        },
        "calendarId": {
            "type": "string",
            "description": "Calendar ID to link this task to (e.g. primary for default Google Calendar)",
        },
        "calendarName": {
            "type": "string",
            "description": "Calendar name to resolve (e.g. Family, Work). Used instead of calendarId.",
        },
        "scheduleNames": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Schedule names to resolve and assign.",
        },
        "updates": {"type": "object", "additionalProperties": True},
        "query": {"type": "string"},
        "includeArchived": {"type": "boolean", "default": False},
    },
)


def _valid_uuid(value: str, field: str) -> str | None:
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError):
        return f"{field} must be a valid UUID"
    return None


def _resolve_calendar_id(client: MynApiClient, name: str) -> str | None:
    data = client.get("/api/v1/customers/calendars")
    calendars = data.get("calendars", []) if isinstance(data, dict) else []
    target = name.lower()
    for calendar in calendars:
        if str(calendar.get("name", "")).lower() == target:
            return calendar.get("id")
    for calendar in calendars:
        if target in str(calendar.get("name", "")).lower():
            return calendar.get("id")
    for calendar in calendars:
        words = str(calendar.get("name", "")).lower().split()
        for word in words:
            if len(word) >= 3 and (target in word or word in target):
                return calendar.get("id")
    return None


def _resolve_schedule_names(client: MynApiClient, names: list[str]) -> list[str]:
    try:
        schedules = client.get("/api/schedules")
        if not isinstance(schedules, list):
            return []
        resolved = []
        for name in names:
            target = name.lower().strip()
            match = next(
                (
                    schedule
                    for schedule in schedules
                    if str(schedule.get("name", "")).lower() == target
                ),
                None,
            )
            if match is None:
                match = next(
                    (
                        schedule
                        for schedule in schedules
                        if target in str(schedule.get("name", "")).lower()
                    ),
                    None,
                )
            if match is not None:
                resolved.append(match["id"])
        return resolved
    except Exception as exc:  # noqa: BLE001
        logger.warning("[myn] schedule-name resolution failed: %s", exc)
        return []


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _overlaps_date_range(
    task: dict[str, Any],
    range_start: date | None,
    range_end: date | None,
) -> bool:
    """Return whether a task's inclusive start/end interval overlaps the filter."""
    task_start = _parse_date(task.get("startDate"))
    task_end = _parse_date(task.get("endDate"))
    if task_start is None and task_end is None:
        return False

    interval_start = task_start or task_end
    interval_end = task_end or task_start
    assert interval_start is not None and interval_end is not None

    if range_start is not None and interval_end < range_start:
        return False
    if range_end is not None and interval_start > range_end:
        return False
    return True


def execute_tasks(client: MynApiClient, **input_data: Any) -> str:
    action = input_data.get("action")

    if action == "list":
        limit = input_data.get("limit", 20)
        offset = input_data.get("offset", 0)
        if type(limit) is not int or limit < 1:
            return tool_error("limit must be a positive integer")
        if type(offset) is not int or offset < 0:
            return tool_error("offset must be a non-negative integer")

        range_start = _parse_date(input_data.get("startDate"))
        range_end = _parse_date(input_data.get("endDate"))
        if input_data.get("startDate") is not None and range_start is None:
            return tool_error("startDate must be an ISO 8601 date")
        if input_data.get("endDate") is not None and range_end is None:
            return tool_error("endDate must be an ISO 8601 date")
        if range_start is not None and range_end is not None and range_start > range_end:
            return tool_error("startDate must be on or before endDate")

        # Fetch without limit/offset, then enforce every advertised filter client-side.
        params = {
            key: input_data[key]
            for key in (
                "status",
                "priority",
                "projectId",
                "startDate",
                "endDate",
            )
            if input_data.get(key) is not None
        }
        params["detail"] = "full"

        def matches_filters(task: dict[str, Any]) -> bool:
            return (
                (not input_data.get("status") or task.get("status") == input_data["status"])
                and (not input_data.get("priority") or task.get("priority") == input_data["priority"])
                and (not input_data.get("projectId") or task.get("projectId") == input_data["projectId"])
                and (
                    range_start is None and range_end is None
                    or _overlaps_date_range(task, range_start, range_end)
                )
            )

        data = fetch_all_unified_tasks(
            client,
            params=params,
            match_fn=matches_filters,
            stop_after=offset + limit,
        )
        if not isinstance(data, UnifiedTaskScan):
            return tool_result(data)

        # Slim the tasks - remove nested schedules, calendar events, household graphs.
        slimmed = []
        for task in data.tasks:
            slim_task = {
                k: v for k, v in task.items()
                if k not in {"schedules", "calendarEvents", "householdGraphs", "nested"}
            }
            slimmed.append(slim_task)

        result = truncate({"tasks": slimmed}, "tasks", limit, offset=offset)
        if not data.complete:
            result.pop("_totalCount", None)
            result["_truncated"] = True
            result["_collectionComplete"] = False
        return tool_result(result)

    if action in {"get", "update", "complete", "archive"}:
        task_id = input_data.get("taskId")
        if not task_id:
            return tool_error(f"taskId is required for {action} action")
        uuid_error = _valid_uuid(task_id, "taskId")
        if uuid_error:
            return tool_error(uuid_error)

    if action == "get":
        return tool_result(client.get(f"/api/v2/unified-tasks/{task_id}"))

    if action == "create":
        for field, message in (
            ("title", "title is required for create action"),
            (
                "priority",
                "priority is required for create action (CRITICAL, OPPORTUNITY_NOW, OVER_THE_HORIZON, PARKING_LOT)",
            ),
            ("taskType", "taskType is required for create action (TASK, HABIT, CHORE)"),
            ("startDate", "startDate is required for create action"),
        ):
            if not input_data.get(field):
                return tool_error(message)

        task_type = input_data["taskType"]
        if task_type in {"HABIT", "CHORE"} and not input_data.get("recurrenceRule"):
            return tool_error(f"{task_type} type requires recurrenceRule")

        body = {
            "id": input_data.get("id") or str(uuid.uuid4()),
            "title": input_data["title"],
            "taskType": task_type,
            "priority": input_data["priority"],
            "startDate": input_data["startDate"],
        }
        for field in ("description", "duration", "projectId", "recurrenceRule"):
            if input_data.get(field):
                body[field] = input_data[field]
        auto_scheduled = input_data.get("isAutoScheduled")
        if auto_scheduled is None:
            auto_scheduled = input_data.get("autoScheduleEnabled")
        body["isAutoScheduled"] = True if auto_scheduled is None else auto_scheduled

        calendar_id = input_data.get("calendarId")
        if not calendar_id and input_data.get("calendarName"):
            calendar_id = _resolve_calendar_id(client, input_data["calendarName"])
        if calendar_id:
            body["calendarId"] = calendar_id

        schedule_names = input_data.get("scheduleNames")
        if schedule_names:
            schedule_ids = _resolve_schedule_names(client, schedule_names)
            if schedule_ids:
                body["scheduleIds"] = schedule_ids

        return tool_result(client.post("/api/v2/unified-tasks", body))

    if action == "update":
        updates = input_data.get("updates")
        if not isinstance(updates, dict) or not updates:
            return tool_error("updates object is required for update action")
        filtered = {
            key: value for key, value in updates.items() if key in ALLOWED_UPDATE_FIELDS
        }
        rejected = [key for key in updates if key not in ALLOWED_UPDATE_FIELDS]
        if not filtered:
            return tool_error(
                f"No valid update fields provided. Rejected fields: {', '.join(rejected)}. "
                f"Allowed fields: {', '.join(sorted(ALLOWED_UPDATE_FIELDS))}"
            )
        data = client.guarded_write(
            "PATCH",
            f"/api/v2/unified-tasks/{task_id}",
            json=filtered,
            get_path=f"/api/v2/unified-tasks/{task_id}",
        )
        if rejected:
            return tool_result({"data": data, "droppedFields": rejected})
        return tool_result(data)

    if action == "complete":
        return tool_result(
            client.guarded_write(
                "POST",
                f"/api/v2/unified-tasks/{task_id}/complete",
                json={},
                get_path=f"/api/v2/unified-tasks/{task_id}",
            )
        )

    if action == "archive":
        return tool_result(
            client.guarded_write(
                "POST",
                f"/api/v2/unified-tasks/{task_id}/archive",
                json={},
                get_path=f"/api/v2/unified-tasks/{task_id}",
            )
        )

    if action == "search":
        params = {}
        if input_data.get("query"):
            params["q"] = input_data["query"]
        if input_data.get("includeArchived"):
            params["includeArchived"] = "true"
        for key in ("limit", "offset"):
            if input_data.get(key) is not None:
                params[key] = input_data[key]
        return tool_result(client.get("/api/v2/search", params=params))

    return tool_error(f"Unknown action: {action}")


def register_tasks_tool(
    ctx: Any,
    client: MynApiClient,
    check_fn: Callable[[], bool],
) -> None:
    register_myn_tool(
        ctx,
        name="myn_tasks",
        schema=TASKS_SCHEMA,
        handler=lambda **kwargs: execute_tasks(client, **kwargs),
        check_fn=check_fn,
        description=(
            "Manage tasks, habits, and chores. Actions: list, get, create, update, complete, archive, search. "
            "SCHEDULING: Tasks default to isAutoScheduled=true. Always assign scheduleNames based on when the task should happen "
            '(e.g. ["Morning"], ["Weekend Morning"], ["Weekday Evening"]). Use calendarName to link to a specific calendar (e.g. "Family"). '
            "CALENDAR EVENTS: When creating a task for a specific date/time event, also create a matching calendar event via myn_calendar."
        ),
        emoji="✅",
    )
