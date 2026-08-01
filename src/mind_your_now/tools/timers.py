"""myn_timers: countdown, alarm, and pomodoro timers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mind_your_now.client import MynApiClient
from mind_your_now.schemas import action_schema
from mind_your_now.tools import register_myn_tool, tool_error, tool_result


TIMERS_SCHEMA = action_schema(
    ["create_countdown", "create_alarm", "list", "cancel", "snooze", "pomodoro"],
    {
        "duration": {"type": "number", "description": "Duration in seconds"},
        "durationMinutes": {"type": "number", "description": "Duration in minutes"},
        "label": {"type": "string", "description": "Timer label/description"},
        "alarmTime": {
            "type": "string",
            "format": "date-time",
            "description": "ISO 8601 datetime for alarm",
        },
        "recurrence": {"type": "string"},
        "sound": {"type": "string"},
        "timerId": {"type": "string"},
        "snoozeMinutes": {"type": "number", "default": 5},
        "workDuration": {"type": "number", "default": 25},
        "breakDuration": {"type": "number", "default": 5},
        "longBreakDuration": {"type": "number", "default": 15},
        "sessions": {"type": "number", "default": 4},
        "autoStart": {"type": "boolean", "default": False},
    },
)


def _provenance_fields(provenance: dict[str, str] | None) -> dict[str, str]:
    if not provenance:
        return {}
    agent_name = provenance.get("source_agent_name") or provenance.get("sourceAgentName")
    channel = provenance.get("source_channel") or provenance.get("sourceChannel")
    fields = {}
    if agent_name:
        fields["sourceAgentName"] = agent_name
    if channel:
        fields["sourceChannel"] = channel
    return fields


def execute_timers(
    client: MynApiClient,
    provenance: dict[str, str] | None = None,
    **input_data: Any,
) -> str:
    action = input_data.get("action")
    source = _provenance_fields(provenance)

    if action == "create_countdown":
        duration_seconds = input_data.get("duration")
        if not duration_seconds and input_data.get("durationMinutes"):
            duration_seconds = input_data["durationMinutes"] * 60
        if not duration_seconds:
            return tool_error(
                "duration (seconds) or durationMinutes is required for create_countdown action"
            )
        body = {
            "name": input_data.get("label") or "Countdown",
            "durationSeconds": duration_seconds,
            **source,
        }
        return tool_result(client.post("/api/v2/timers/countdown", body))

    if action == "create_alarm":
        if not input_data.get("alarmTime"):
            return tool_error("alarmTime is required for create_alarm action")
        body = {
            "name": input_data.get("label") or "Alarm",
            "alarmTime": input_data["alarmTime"],
            **source,
        }
        if input_data.get("recurrence"):
            body["recurrence"] = input_data["recurrence"]
        if input_data.get("sound"):
            body["completionSound"] = input_data["sound"]
        return tool_result(client.post("/api/v2/timers/alarm", body))

    if action == "list":
        return tool_result(client.get("/api/v2/timers"))

    if action in {"cancel", "snooze"}:
        timer_id = input_data.get("timerId")
        if not timer_id:
            return tool_error(f"timerId is required for {action} action")
        if action == "cancel":
            return tool_result(
                client.guarded_write(
                    "POST",
                    f"/api/v2/timers/{timer_id}/cancel",
                    json={},
                    get_path=f"/api/v2/timers/{timer_id}",
                )
            )
        snooze_minutes = input_data.get("snoozeMinutes")
        return tool_result(
            client.guarded_write(
                "POST",
                f"/api/v2/timers/{timer_id}/snooze",
                json={"snoozeMinutes": 5 if snooze_minutes is None else snooze_minutes},
                get_path=f"/api/v2/timers/{timer_id}",
            )
        )

    if action == "pomodoro":
        work = input_data.get("workDuration")
        short_break = input_data.get("breakDuration")
        long_break = input_data.get("longBreakDuration")
        sessions = input_data.get("sessions")
        auto_start = input_data.get("autoStart")
        body = {
            "name": input_data.get("label") or "Pomodoro",
            "type": "POMODORO",
            "durationSeconds": (25 if work is None else work) * 60,
            "breakDuration": (5 if short_break is None else short_break) * 60,
            "longBreakDuration": (15 if long_break is None else long_break) * 60,
            "sessions": 4 if sessions is None else sessions,
            "autoStart": False if auto_start is None else auto_start,
            **source,
        }
        return tool_result(client.post("/api/v2/timers/countdown", body))

    return tool_error(f"Unknown action: {action}")


def register_timers_tool(
    ctx: Any,
    client: MynApiClient,
    check_fn: Callable[[], bool],
    provenance: dict[str, str] | None = None,
) -> None:
    register_myn_tool(
        ctx,
        name="myn_timers",
        schema=TIMERS_SCHEMA,
        handler=lambda **kwargs: execute_timers(client, provenance, **kwargs),
        check_fn=check_fn,
        description=(
            "Manage timers for the USER (not for yourself). Timers create notifications on the user's phone/device. "
            "Actions: create_countdown, create_alarm, list, cancel, snooze, pomodoro. "
            "Only create timers when the user explicitly asks for a reminder or timer."
        ),
        emoji="⏱️",
    )
