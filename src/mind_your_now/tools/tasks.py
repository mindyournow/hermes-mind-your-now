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
    # MIN-1085: plans. parentTaskId moves a task into a plan ("" takes it out);
    # position orders the plan's steps.
    "parentTaskId",
    "position",
}

SHARE_TYPES = ["VIEW", "EDIT", "DELEGATE"]
SHARE_RESPONSES = ["ACCEPT", "DECLINE"]
SHARE_ACTIONS = {"share", "list_shares", "revoke_share", "respond_share"}
PLAN_ACTIONS = {"plan", "add_steps", "reorder_steps"}

TASKS_SCHEMA = action_schema(
    [
        "list",
        "get",
        "create",
        "update",
        "complete",
        "archive",
        "search",
        "share",
        "list_shares",
        "revoke_share",
        "respond_share",
        "shared_with_me",
        "create_plan",
        "plan",
        "add_steps",
        "reorder_steps",
    ],
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
        "memberIds": {
            "type": "array",
            "items": {"type": "string", "format": "uuid"},
            "minItems": 1,
            "description": (
                "For share: household member IDs (the `id` field from myn_household members, "
                "not customerId) to share the task with."
            ),
        },
        "memberId": {
            "type": "string",
            "format": "uuid",
            "description": "For revoke_share: the household member ID whose access to remove.",
        },
        "shareType": {
            "type": "string",
            "enum": SHARE_TYPES,
            "default": "EDIT",
            "description": (
                "For share: VIEW = read-only, EDIT = can view and modify (default, as in the MYN app), "
                "DELEGATE = transfers ownership of the task to the recipient."
            ),
        },
        "message": {
            "type": "string",
            "maxLength": 500,
            "description": "For share: optional note included in the recipient's notification.",
        },
        "response": {
            "type": "string",
            "enum": SHARE_RESPONSES,
            "description": "For respond_share: ACCEPT or DECLINE a pending share sent to the user.",
        },
        "note": {
            "type": "string",
            "description": "For respond_share: optional note explaining the response.",
        },
        "parentTaskId": {
            "type": "string",
            "format": "uuid",
            "description": "For create: make the new task a step of this plan (a task id).",
        },
        "position": {
            "type": "integer",
            "description": "For create: the step's place in its plan (0 is first).",
        },
        "steps": {
            "type": "array",
            "minItems": 1,
            "maxItems": 50,
            "description": (
                "For create_plan/add_steps: new steps in the order they happen. Each has a title and "
                "optional startDate (YYYY-MM-DD) and priority; both default to the plan's."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "minLength": 1, "maxLength": 200},
                    "startDate": {"type": "string", "format": "date"},
                    "priority": {"type": "string", "enum": PRIORITIES},
                },
                "required": ["title"],
            },
        },
        "stepIds": {
            "type": "array",
            "items": {"type": "string", "format": "uuid"},
            "description": (
                "For create_plan/add_steps: existing task ids to move into the plan as steps, in order. "
                "For reorder_steps: the plan's step ids in the new order (steps left out keep their "
                "order after these)."
            ),
        },
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

    if action in {"get", "update", "complete", "archive", *SHARE_ACTIONS, *PLAN_ACTIONS}:
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
        for field in ("description", "duration", "projectId", "recurrenceRule", "parentTaskId"):
            if input_data.get(field):
                body[field] = input_data[field]
        if type(input_data.get("position")) is int:
            body["position"] = input_data["position"]
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

    if action == "share":
        member_ids = input_data.get("memberIds")
        if isinstance(member_ids, str):
            member_ids = [member_ids]
        if not isinstance(member_ids, list) or not member_ids:
            return tool_error(
                "memberIds is required for share action. Use myn_household members and pass "
                "each member's id (not customerId)."
            )
        for member_id in member_ids:
            uuid_error = _valid_uuid(member_id, "memberIds entry")
            if uuid_error:
                return tool_error(uuid_error)
        share_type = str(input_data.get("shareType") or "EDIT").upper()
        if share_type not in SHARE_TYPES:
            return tool_error(f"shareType must be one of {', '.join(SHARE_TYPES)}")
        body: dict[str, Any] = {
            "member_ids": list(dict.fromkeys(member_ids)),
            "share_type": share_type,
        }
        if input_data.get("message"):
            body["message"] = input_data["message"]
        return tool_result(client.post(f"/api/v2/unified-tasks/{task_id}/share", body))

    if action == "list_shares":
        return tool_result(client.get(f"/api/v2/unified-tasks/{task_id}/shares"))

    if action == "revoke_share":
        member_id = input_data.get("memberId")
        if not member_id:
            return tool_error("memberId is required for revoke_share action")
        uuid_error = _valid_uuid(member_id, "memberId")
        if uuid_error:
            return tool_error(uuid_error)
        client.delete(f"/api/v2/unified-tasks/{task_id}/share/{member_id}")
        return tool_result({"revoked": True, "taskId": task_id, "memberId": member_id})

    if action == "respond_share":
        response = str(input_data.get("response") or "").upper()
        if response not in SHARE_RESPONSES:
            return tool_error("response is required for respond_share action (ACCEPT or DECLINE)")
        body = {"response": response}
        if input_data.get("note"):
            body["note"] = input_data["note"]
        data = client.post(f"/api/v2/unified-tasks/{task_id}/share/respond", body)
        if data is None:
            data = {"taskId": task_id, "response": response}
        return tool_result(data)

    if action == "create_plan":
        return _create_plan(client, input_data)

    if action == "plan":
        return tool_result(client.get(f"/api/v2/unified-tasks/{task_id}/plan"))

    if action == "add_steps":
        error = _attach_steps(client, task_id, input_data)
        if error:
            return tool_error(error)
        return tool_result(client.get(f"/api/v2/unified-tasks/{task_id}/plan"))

    if action == "reorder_steps":
        step_ids = input_data.get("stepIds")
        if not isinstance(step_ids, list) or not step_ids:
            return tool_error("stepIds is required for reorder_steps action")
        plan = client.get(f"/api/v2/unified-tasks/{task_id}/plan")
        current = [step.get("id") for step in (plan or {}).get("steps", [])]
        unknown = [step_id for step_id in step_ids if step_id not in current]
        if unknown:
            return tool_error(f"Not steps of this plan: {', '.join(unknown)}")
        order = list(dict.fromkeys(step_ids)) + [s for s in current if s not in step_ids]
        for position, step_id in enumerate(order):
            if current.index(step_id) != position:
                client.guarded_write(
                    "PATCH",
                    f"/api/v2/unified-tasks/{step_id}",
                    json={"position": position},
                    get_path=f"/api/v2/unified-tasks/{step_id}",
                )
        return tool_result(client.get(f"/api/v2/unified-tasks/{task_id}/plan"))

    if action == "shared_with_me":
        return tool_result(client.get("/api/v2/unified-tasks/shared-with-me"))

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


def _attach_steps(client: MynApiClient, plan_id: str, input_data: dict[str, Any]) -> str | None:
    """Append new steps, then existing tasks moved in, after the plan's last step."""
    steps = input_data.get("steps") or []
    moved = input_data.get("stepIds") or []
    if not isinstance(steps, list) or not isinstance(moved, list) or not (steps or moved):
        return "steps or stepIds is required"
    for step in steps:
        if not isinstance(step, dict) or not step.get("title"):
            return "every step needs a title"
    for step_id in moved:
        uuid_error = _valid_uuid(step_id, "stepIds entry")
        if uuid_error:
            return uuid_error

    plan_task = client.get(f"/api/v2/unified-tasks/{plan_id}")
    existing = client.get(f"/api/v2/unified-tasks/{plan_id}/plan") or {}
    position = len(existing.get("steps", []))
    for step in steps:
        client.post(
            "/api/v2/unified-tasks",
            {
                "id": str(uuid.uuid4()),
                "title": step["title"],
                "taskType": "TASK",
                "priority": step.get("priority") or plan_task.get("priority") or "OPPORTUNITY_NOW",
                "startDate": step.get("startDate") or plan_task.get("startDate") or date.today().isoformat(),
                "isAutoScheduled": False,
                "parentTaskId": plan_id,
                "position": position,
            },
        )
        position += 1
    for step_id in moved:
        client.guarded_write(
            "PATCH",
            f"/api/v2/unified-tasks/{step_id}",
            json={"parentTaskId": plan_id, "position": position},
            get_path=f"/api/v2/unified-tasks/{step_id}",
        )
        position += 1
    return None


def _create_plan(client: MynApiClient, input_data: dict[str, Any]) -> str:
    """One plan task, then its steps in order (MIN-1085)."""
    if not input_data.get("title"):
        return tool_error("title is required for create_plan action")
    steps = input_data.get("steps") or []
    if not steps and not input_data.get("stepIds"):
        return tool_error("create_plan needs steps or stepIds")
    first = steps[0] if steps and isinstance(steps[0], dict) else {}
    plan_id = input_data.get("id") or str(uuid.uuid4())
    client.post(
        "/api/v2/unified-tasks",
        {
            "id": plan_id,
            "title": input_data["title"],
            "taskType": "TASK",
            "priority": input_data.get("priority") or first.get("priority") or "OPPORTUNITY_NOW",
            "startDate": input_data.get("startDate") or first.get("startDate") or date.today().isoformat(),
            "isAutoScheduled": False,
            **({"description": input_data["description"]} if input_data.get("description") else {}),
        },
    )
    error = _attach_steps(client, plan_id, input_data)
    if error:
        return tool_error(error)
    return tool_result(client.get(f"/api/v2/unified-tasks/{plan_id}/plan"))


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
            "Manage tasks, habits, and chores. Actions: list, get, create, update, complete, archive, search, "
            "share, list_shares, revoke_share, respond_share, shared_with_me, create_plan, plan, add_steps, "
            "reorder_steps. "
            "PLANS: a multi-step effort (a move, a trip, a launch) is ONE plan task whose steps are done in "
            "order, not a pile of tasks. create_plan takes a title and steps in order (and/or stepIds of existing "
            "tasks to group). The plan shows as one item in the zone and on the date of its current step (the "
            "first step not done). plan (taskId = the plan or any step) answers \"what's next\": currentStep. "
            "add_steps appends steps; reorder_steps takes stepIds in the new order. update with parentTaskId "
            "and position also moves a single task into a plan; parentTaskId \"\" takes it out. "
            "SHARING: share gives specific household members access to one task and sends each recipient a "
            "notification (push + inbox), so confirm the recipients with the user first and call list_shares "
            "to avoid re-sharing. memberIds are household member ids from myn_household members. Being in the "
            "same household does NOT give someone access to a task; only a share does. Only the task owner can "
            "share, list_shares, or revoke_share; chores, habits, recurring, completed, and archived tasks cannot "
            "be shared. shared_with_me lists shares sent to the user; respond_share accepts or declines one. "
            "SCHEDULING: Tasks default to isAutoScheduled=true. Always assign scheduleNames based on when the task should happen "
            '(e.g. ["Morning"], ["Weekend Morning"], ["Weekday Evening"]). Use calendarName to link to a specific calendar (e.g. "Family"). '
            "CALENDAR EVENTS: When creating a task for a specific date/time event, also create a matching calendar event via myn_calendar."
        ),
        emoji="✅",
    )
