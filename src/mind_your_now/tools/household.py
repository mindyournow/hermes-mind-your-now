"""myn_household: household members, invites, and chores."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any

from mind_your_now.client import MynApiClient
from mind_your_now.schemas import action_schema
from mind_your_now.tools import register_myn_tool, tool_error, tool_result


HOUSEHOLD_SCHEMA = action_schema(
    ["members", "invite", "chores", "chore_schedule", "chore_complete"],
    {
        "householdId": {"type": "string", "format": "uuid"},
        "email": {"type": "string", "format": "email"},
        "role": {"type": "string", "enum": ["member", "admin"]},
        "message": {"type": "string"},
        "choreId": {"type": "string", "format": "uuid"},
        "completedBy": {"type": "string", "format": "uuid"},
        "note": {"type": "string"},
        "date": {"type": "string", "format": "date"},
        "weekStart": {"type": "string", "format": "date"},
    },
)


def _household_id(client: MynApiClient, provided: str | None) -> str | None:
    if provided:
        return provided
    household = client.get("/api/v1/households/current")
    return household.get("id") if isinstance(household, dict) else None


def execute_household(client: MynApiClient, **input_data: Any) -> str:
    action = input_data.get("action")
    if action not in {
        "members",
        "invite",
        "chores",
        "chore_schedule",
        "chore_complete",
    }:
        return tool_error(f"Unknown action: {action}")

    if action == "chore_complete":
        chore_id = input_data.get("choreId")
        if not chore_id:
            return tool_error("choreId is required for chore_complete action")
        body = {}
        if input_data.get("completedBy"):
            body["completedBy"] = input_data["completedBy"]
        if input_data.get("note"):
            body["note"] = input_data["note"]
        return tool_result(
            client.guarded_write(
                "POST",
                f"/api/v2/chores/instances/{chore_id}/complete",
                json=body,
                get_path=f"/api/v2/chores/instances/{chore_id}",
            )
        )

    if action == "invite" and not input_data.get("email"):
        return tool_error("email is required for invite action")

    household_id = _household_id(client, input_data.get("householdId"))
    if not household_id:
        return tool_error("No household found. Please specify householdId.")

    if action == "members":
        return tool_result(
            client.get(f"/api/v1/households/{household_id}/members")
        )

    if action == "invite":
        body = {"email": input_data["email"]}
        for field in ("role", "message"):
            if input_data.get(field):
                body[field] = input_data[field]
        return tool_result(
            client.post(f"/api/v1/households/{household_id}/invites", body)
        )

    if action == "chores":
        return tool_result(
            client.get("/api/v2/chores/today", params={"householdId": household_id})
        )

    today = datetime.now(timezone.utc).date()
    start_date = input_data.get("date") or input_data.get("weekStart") or today.isoformat()
    if input_data.get("date"):
        end_date = input_data["date"]
    else:
        end_date = (date.fromisoformat(start_date) + timedelta(days=7)).isoformat()
    return tool_result(
        client.get(
            "/api/v2/chores/schedule/range",
            params={
                "householdId": household_id,
                "startDate": start_date,
                "endDate": end_date,
            },
        )
    )


def register_household_tool(
    ctx: Any,
    client: MynApiClient,
    check_fn: Callable[[], bool],
) -> None:
    register_myn_tool(
        ctx,
        name="myn_household",
        schema=HOUSEHOLD_SCHEMA,
        handler=lambda **kwargs: execute_household(client, **kwargs),
        check_fn=check_fn,
        description=(
            "Manage household members, invites, and chores. Actions: members, "
            "invite, chores, chore_schedule, chore_complete."
        ),
        emoji="🏠",
    )
