"""myn_calendar: calendar events and meetings."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from mind_your_now.client import MynApiClient
from mind_your_now.schemas import action_schema
from mind_your_now.tools import register_myn_tool, tool_error, tool_result


logger = logging.getLogger(__name__)
_CACHE_TTL_SECONDS = 10 * 60
_CACHE_MAX_SIZE = 500
_event_detail_cache: dict[str, tuple[dict[str, Any], str, float]] = {}

CALENDAR_SCHEMA = action_schema(
    [
        "list_calendars",
        "list_events",
        "get_event",
        "create_event",
        "update_event",
        "delete_event",
        "move_event",
        "meetings",
    ],
    {
        "startDate": {"type": "string", "format": "date-time"},
        "endDate": {"type": "string", "format": "date-time"},
        "calendarId": {"type": "string"},
        "calendarName": {
            "type": "string",
            "description": "Calendar name to resolve to ID (e.g. Family, Work).",
        },
        "includeAllDay": {"type": "boolean", "default": True},
        "limit": {"type": "number", "default": 50},
        "title": {"type": "string", "minLength": 1, "maxLength": 200},
        "description": {"type": "string", "maxLength": 2000},
        "startTime": {"type": "string", "format": "date-time"},
        "endTime": {"type": "string", "format": "date-time"},
        "isAllDay": {"type": "boolean", "default": False},
        "location": {"type": "string"},
        "attendees": {"type": "array", "items": {"type": "string"}},
        "recurrence": {"type": "string"},
        "reminders": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "minutes": {"type": "number"},
                    "method": {"type": "string", "enum": ["popup", "email"]},
                },
                "required": ["minutes", "method"],
            },
        },
        "timezone": {"type": "string"},
        "eventId": {"type": "string"},
        "newTitle": {"type": "string"},
        "newDescription": {"type": "string"},
        "newLocation": {"type": "string"},
        "newStartTime": {"type": "string", "format": "date-time"},
        "newEndTime": {"type": "string", "format": "date-time"},
        "newAttendees": {"type": "array", "items": {"type": "string"}},
        "addAttendees": {"type": "array", "items": {"type": "string"}},
        "destinationCalendarId": {"type": "string"},
        "destinationCalendarName": {"type": "string"},
        "sourceCalendarId": {"type": "string"},
        "includePast": {"type": "boolean", "default": False},
        "daysAhead": {"type": "number", "default": 7},
    },
)


def _hash_event(data: dict[str, Any]) -> str:
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.md5(encoded).hexdigest()  # noqa: S324 - change detection only


def _get_cached(event_id: str) -> tuple[dict[str, Any], str, float] | None:
    cached = _event_detail_cache.get(event_id)
    if cached and time.monotonic() - cached[2] <= _CACHE_TTL_SECONDS:
        return cached
    _event_detail_cache.pop(event_id, None)
    return None


def _set_cached(event_id: str, data: dict[str, Any]) -> None:
    if len(_event_detail_cache) >= _CACHE_MAX_SIZE:
        _event_detail_cache.pop(next(iter(_event_detail_cache)))
    _event_detail_cache[event_id] = (data, _hash_event(data), time.monotonic())


def slim_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    slimmed = []
    for event in events:
        item = {
            key: value
            for key, value in event.items()
            if key not in {"description", "attendees", "transparency"}
        }
        description = event.get("description")
        if isinstance(description, str):
            text = description
            if "<" in text and ">" in text:
                text = re.sub(r"<[^>]*>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
            if text:
                item["description"] = text if len(text) <= 200 else text[:200] + "..."
        slimmed.append(item)
    return slimmed


def resolve_calendar_id(client: MynApiClient, name: str) -> str | None:
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
        for word in str(calendar.get("name", "")).lower().split():
            if len(word) >= 3 and (target in word or word in target):
                return calendar.get("id")
    return None


def _to_iso_datetime(value: str, date: str | None = None) -> str:
    if "T" in value or re.match(r"^\d{4}-\d{2}-\d{2}", value):
        return value
    if date and re.match(r"^\d{2}:\d{2}", value):
        normalized_time = value + ":00" if re.fullmatch(r"\d{2}:\d{2}", value) else value
        return f"{date[:10]}T{normalized_time}"
    return value


def _is_valid_email(value: str) -> bool:
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value))


def _resolve_attendees(client: MynApiClient, attendees: list[str]) -> list[str]:
    emails: list[str] = []
    names: list[str] = []
    invite_all = False
    for attendee in attendees:
        normalized = attendee.lower().strip()
        if normalized in {"family", "everyone", "all", "whole family", "all family"}:
            invite_all = True
        elif "@" in attendee:
            if _is_valid_email(attendee):
                emails.append(attendee)
            else:
                logger.warning("[myn_calendar] Skipping malformed email address: %s", attendee)
        else:
            names.append(normalized)

    if not invite_all and not names:
        return emails
    try:
        household = client.get("/api/v1/households/current")
        household_id = household.get("id") if isinstance(household, dict) else None
        if not household_id:
            return emails
        response = client.get(f"/api/v1/households/{household_id}/members")
        members = response.get("members", []) if isinstance(response, dict) else []
        if invite_all:
            for member in members:
                email = member.get("email")
                if email and email not in emails:
                    emails.append(email)
        else:
            for name in names:
                match = next(
                    (
                        member
                        for member in members
                        if name in str(member.get("name", "")).lower()
                        or str(member.get("name", "")).lower().split(" ")[0] in name
                    ),
                    None,
                )
                email = match.get("email") if match else None
                if email and email not in emails:
                    emails.append(email)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[myn_calendar] Household attendee lookup failed: %s", exc)
    return emails


def _detect_family_calendar(client: MynApiClient) -> str | None:
    try:
        data = client.get("/api/v1/customers/calendars")
        calendars = data.get("calendars", []) if isinstance(data, dict) else []
        for calendar in calendars:
            name = str(calendar.get("name", "")).lower()
            if calendar.get("using") and any(
                keyword in name for keyword in ("family", "shared", "household")
            ):
                return calendar.get("id")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[myn_calendar] Family calendar lookup failed: %s", exc)
    return None


def _local_timezone() -> str:
    tzinfo = datetime.now().astimezone().tzinfo
    return getattr(tzinfo, "key", None) or str(tzinfo) or "UTC"


def execute_calendar(client: MynApiClient, **input_data: Any) -> str:
    action = input_data.get("action")

    if action == "list_calendars":
        return tool_result(client.get("/api/v1/customers/calendars"))

    if action == "list_events":
        params: dict[str, Any] = {}
        if input_data.get("startDate"):
            params["start"] = input_data["startDate"]
        if input_data.get("endDate"):
            params["end"] = input_data["endDate"]
        elif input_data.get("daysAhead"):
            params["end"] = (
                datetime.now(timezone.utc)
                + timedelta(days=float(input_data["daysAhead"]))
            ).isoformat().replace("+00:00", "Z")
        if input_data.get("limit") is not None:
            params["limit"] = input_data["limit"]
        data = client.get("/api/v2/calendar/events", params=params)
        if isinstance(data, dict) and isinstance(data.get("events"), list):
            data["events"] = slim_events(data["events"])
        return tool_result(data)

    if action in {"get_event", "update_event", "delete_event", "move_event"}:
        event_id = input_data.get("eventId")
        if not event_id:
            return tool_error(f"eventId is required for {action} action")

    if action == "get_event":
        cached = _get_cached(event_id)
        data = client.get(f"/api/v2/calendar/events/{event_id}")
        if not data:
            return tool_error(f"Event not found: {event_id}")
        current_hash = _hash_event(data)
        changed = cached is None or cached[1] != current_hash
        _set_cached(event_id, data)
        return tool_result({**data, "_cached": not changed, "_hash": current_hash})

    if action == "create_event":
        if not input_data.get("title"):
            return tool_error("title is required for create_event action")
        if not input_data.get("startTime"):
            return tool_error(
                'startTime is required for create_event action (ISO 8601 format, e.g. "2026-03-08T16:30:00")'
            )
        if not input_data.get("isAllDay") and not input_data.get("endTime"):
            return tool_error("endTime is required for non-all-day events")

        date = str(input_data.get("startDate", ""))[:10] or None
        start_time = _to_iso_datetime(input_data["startTime"], date)
        end_time = (
            _to_iso_datetime(input_data["endTime"], date)
            if input_data.get("endTime")
            else None
        )
        attendees = (
            _resolve_attendees(client, input_data["attendees"])
            if input_data.get("attendees")
            else None
        )
        calendar_id = input_data.get("calendarId")
        if not calendar_id and input_data.get("calendarName"):
            calendar_id = resolve_calendar_id(client, input_data["calendarName"])
        if not calendar_id and attendees:
            calendar_id = _detect_family_calendar(client)

        body: dict[str, Any] = {
            "title": input_data["title"],
            "startTime": start_time,
            "isAllDay": input_data.get("isAllDay", False),
        }
        if not input_data.get("isAllDay") and end_time:
            body["endTime"] = end_time
        for field in ("description", "location", "timezone", "recurrence"):
            if input_data.get(field):
                body[field] = input_data[field]
        if calendar_id:
            body["calendarId"] = calendar_id
        if attendees:
            body["attendees"] = attendees
        return tool_result(client.post("/api/v2/calendar/standalone-events", body))

    if action == "update_event":
        updates: dict[str, Any] = {}
        mapping = {
            "newTitle": "title",
            "newDescription": "description",
            "newLocation": "location",
        }
        for source, destination in mapping.items():
            if input_data.get(source):
                updates[destination] = input_data[source]
        timezone_name = input_data.get("timezone") or _local_timezone()
        if input_data.get("newStartTime"):
            updates["start"] = {
                "dateTime": input_data["newStartTime"],
                "timeZone": timezone_name,
            }
        if input_data.get("newEndTime"):
            updates["end"] = {
                "dateTime": input_data["newEndTime"],
                "timeZone": timezone_name,
            }
        if input_data.get("newAttendees"):
            emails = _resolve_attendees(client, input_data["newAttendees"])
            updates["attendees"] = [{"email": email} for email in emails]
        elif input_data.get("addAttendees"):
            try:
                current = client.get(f"/api/v2/calendar/events/{event_id}")
                existing = [
                    attendee["email"]
                    for attendee in current.get("attendees", [])
                    if attendee.get("email")
                ]
            except Exception as exc:  # noqa: BLE001
                logger.warning("[myn_calendar] Current attendee lookup failed: %s", exc)
                existing = []
            added = _resolve_attendees(client, input_data["addAttendees"])
            updates["attendees"] = [
                {"email": email} for email in dict.fromkeys([*existing, *added])
            ]
        if not updates:
            return tool_error(
                "No update fields provided. Use newTitle, newDescription, newLocation, newStartTime, newEndTime, newAttendees, or addAttendees."
            )
        calendar_id = input_data.get("calendarId")
        if not calendar_id and input_data.get("calendarName"):
            calendar_id = resolve_calendar_id(client, input_data["calendarName"]) or "primary"
        data = client.patch(
            f"/api/v2/calendar/standalone-events/{event_id}",
            updates,
            params={"calendarId": calendar_id or "primary"},
        )
        return tool_result({"updated": True, "eventId": event_id, **(data or {})})

    if action == "delete_event":
        calendar_id = input_data.get("calendarId")
        client.delete(
            f"/api/v2/calendar/standalone-events/{event_id}",
            params={"calendarId": calendar_id or "primary"},
        )
        return tool_result({"deleted": True, "eventId": event_id})

    if action == "meetings":
        now = datetime.now().astimezone()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0) if input_data.get("includePast") else now
        end = now + timedelta(days=float(input_data.get("daysAhead", 7)))
        params = {"start": start.isoformat(), "end": end.isoformat()}
        if input_data.get("limit") is not None:
            params["limit"] = input_data["limit"]
        data = client.get("/api/v2/calendar/events", params=params)
        if isinstance(data, dict) and isinstance(data.get("events"), list):
            meetings = [event for event in data["events"] if event.get("attendees")]
            data["events"] = slim_events(meetings)
            data["total"] = len(meetings)
        return tool_result(data)

    if action == "move_event":
        destination_id = input_data.get("destinationCalendarId")
        if not destination_id and input_data.get("destinationCalendarName"):
            destination_id = resolve_calendar_id(
                client, input_data["destinationCalendarName"]
            )
        if not destination_id:
            return tool_error(
                "destinationCalendarId or destinationCalendarName is required for move_event action"
            )
        data = client.post(
            f"/api/v2/calendar/standalone-events/{event_id}/move",
            {},
            params={
                "sourceCalendarId": input_data.get("sourceCalendarId") or "primary",
                "destinationCalendarId": destination_id,
            },
        )
        return tool_result(data)

    return tool_error(f"Unknown action: {action}")


def register_calendar_tool(
    ctx: Any,
    client: MynApiClient,
    check_fn: Callable[[], bool],
) -> None:
    register_myn_tool(
        ctx,
        name="myn_calendar",
        schema=CALENDAR_SCHEMA,
        handler=lambda **kwargs: execute_calendar(client, **kwargs),
        check_fn=check_fn,
        description=(
            "Manage calendar events and meetings. Actions: list_calendars, list_events, get_event, create_event, update_event, delete_event, move_event, meetings. "
            'CALENDAR SELECTION: Use calendarName (e.g. "Family") instead of calendarId when you know the name. '
            "HOUSEHOLD AWARENESS: Events involving household members auto-detect the family calendar. "
            'SHARING: Use attendees with email addresses, household member first names, or "family"/"everyone". '
            "MOVE: Use move_event with eventId and destinationCalendarName."
        ),
        emoji="📅",
    )
