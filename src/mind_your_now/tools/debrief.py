"""myn_debrief: Daily Debrief generation and corrections."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mind_your_now.client import MynApiClient
from mind_your_now.schemas import action_schema
from mind_your_now.tools import register_myn_tool, tool_error, tool_result


DEBRIEF_SCHEMA = action_schema(
    ["status", "generate", "get", "apply_correction", "complete_session"],
    {
        "type": {
            "type": "string",
            "enum": ["DAILY", "EVENING", "WEEKLY", "WEEKLY_AND_DAILY", "ON_DEMAND"],
            "description": "Type of debrief to generate. Defaults to DAILY.",
        },
        "context": {
            "type": "string",
            "description": "Additional context for briefing generation",
        },
        "focusAreas": {"type": "array", "items": {"type": "string"}},
        "debriefId": {"type": "string", "format": "uuid"},
        "correctionId": {"type": "string", "format": "uuid"},
        "correctionType": {
            "type": "string",
            "enum": [
                "TASK_COMPLETED",
                "TASK_MISSED",
                "TASK_RESCHEDULED",
                "TASK_ADDED",
                "PRIORITY_CHANGED",
                "OTHER",
            ],
        },
        "correctionData": {"type": "object", "additionalProperties": True},
        "reason": {"type": "string"},
        "sessionSummary": {"type": "string"},
        "decisions": {"type": "array", "items": {"type": "string"}},
    },
)


def execute_debrief(client: MynApiClient, **input_data: Any) -> str:
    action = input_data.get("action")

    if action == "status":
        return tool_result(client.get("/api/v2/debrief/status"))

    if action == "generate":
        body: dict[str, Any] = {"type": input_data.get("type") or "DAILY"}
        if input_data.get("context"):
            body["context"] = input_data["context"]
        if input_data.get("focusAreas"):
            body["focusAreas"] = input_data["focusAreas"]
        return tool_result(client.post("/api/v2/debrief/generate", body))

    if action == "get":
        return tool_result(client.get("/api/v2/debrief/current"))

    if action == "apply_correction":
        correction_type = input_data.get("correctionType")
        if not correction_type:
            return tool_error(
                "correctionType is required for apply_correction action"
            )
        body = {"type": correction_type}
        if input_data.get("correctionData"):
            body["data"] = input_data["correctionData"]
        if input_data.get("reason"):
            body["reason"] = input_data["reason"]
        return tool_result(
            client.guarded_write(
                "POST",
                "/api/v2/debrief/corrections/apply",
                json=body,
                get_path="/api/v2/debrief/current",
            )
        )

    if action == "complete_session":
        body = {}
        if input_data.get("sessionSummary"):
            body["summary"] = input_data["sessionSummary"]
        if input_data.get("decisions"):
            body["decisions"] = input_data["decisions"]
        return tool_result(
            client.guarded_write(
                "POST",
                "/api/v2/debrief/complete",
                json=body,
                get_path="/api/v2/debrief/current",
            )
        )

    return tool_error(f"Unknown action: {action}")


def register_debrief_tool(
    ctx: Any,
    client: MynApiClient,
    check_fn: Callable[[], bool],
) -> None:
    register_myn_tool(
        ctx,
        name="myn_debrief",
        schema=DEBRIEF_SCHEMA,
        handler=lambda **kwargs: execute_debrief(client, **kwargs),
        check_fn=check_fn,
        description=(
            "Generate and manage Daily Debrief sessions. Actions: status, "
            "generate, get, apply_correction, complete_session."
        ),
        emoji="🧭",
    )
