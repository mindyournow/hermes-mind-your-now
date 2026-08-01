"""myn_lists: grocery and shopping list management."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mind_your_now.client import MynApiClient
from mind_your_now.schemas import action_schema
from mind_your_now.tools import register_myn_tool, tool_error, tool_result


LISTS_SCHEMA = action_schema(
    [
        "get",
        "add",
        "toggle",
        "bulk_add",
        "update",
        "delete",
        "delete_checked",
        "convert_to_tasks",
    ],
    {
        "householdId": {"type": "string", "format": "uuid"},
        "item": {"type": "string", "minLength": 1},
        "items": {"type": "array", "items": {"type": "string"}},
        "category": {"type": "string"},
        "quantity": {"type": "string"},
        "notes": {"type": "string"},
        "itemId": {"type": "string", "format": "uuid"},
        "checked": {"type": "boolean"},
        "dryRun": {"type": "boolean", "description": "Preview the action without making changes (delete_checked, convert_to_tasks only)"},
        "uncheckedOnly": {"type": "boolean", "default": True},
        "priority": {
            "type": "string",
            "enum": [
                "CRITICAL",
                "OPPORTUNITY_NOW",
                "OVER_THE_HORIZON",
                "PARKING_LOT",
            ],
        },
    },
)


def _resolve_household_id(
    client: MynApiClient,
    household_id: str | None,
) -> str | None:
    if household_id:
        return household_id
    household = client.get("/api/v1/households/current")
    return household.get("id") if isinstance(household, dict) else None


def execute_lists(client: MynApiClient, **input_data: Any) -> str:
    action = input_data.get("action")
    if action not in {
        "get",
        "add",
        "toggle",
        "bulk_add",
        "update",
        "delete",
        "delete_checked",
        "convert_to_tasks",
    }:
        return tool_error(f"Unknown action: {action}")

    if action == "add" and not input_data.get("item"):
        return tool_error("item is required for add action")
    if action in {"toggle", "update", "delete"} and not input_data.get("itemId"):
        return tool_error(f"itemId is required for {action} action")
    if action == "bulk_add" and not input_data.get("items"):
        return tool_error("items array is required for bulk_add action")

    household_id = _resolve_household_id(client, input_data.get("householdId"))
    if not household_id:
        return tool_error("No household found. Please specify householdId.")
    base_path = f"/api/v1/households/{household_id}/grocery-list"

    if action == "get":
        return tool_result(client.get(base_path))

    if action == "add":
        body = {"name": input_data["item"]}
        for field in ("category", "quantity", "notes"):
            if input_data.get(field):
                body[field] = input_data[field]
        return tool_result(client.post(base_path, body))

    if action == "toggle":
        item_id = input_data["itemId"]
        requested_checked = input_data.get("checked")
        # Read collection to find current state
        items_data = client.get(base_path)
        current = None

        # Try different response shapes
        if isinstance(items_data, list):
            # Bare array response
            current = next((item for item in items_data if item.get("id") == item_id), None)
        elif isinstance(items_data, dict):
            # Check if it's a wrapped collection
            if "items" in items_data:
                current = next((item for item in items_data["items"] if item.get("id") == item_id), None)
            # Or it might be the single item itself (for some implementations)
            elif items_data.get("id") == item_id:
                current = items_data

        if current:
            current_checked = current.get("checked", False)
            # Only toggle if the requested state differs from current state
            if requested_checked is not None and current_checked == requested_checked:
                # Already in desired state, return unchanged
                return tool_result(current)
        # Issue toggle PATCH
        return tool_result(
            client.patch(f"{base_path}/{item_id}/toggle", {})
        )

    if action == "bulk_add":
        entries = []
        for item in input_data["items"]:
            entry = {"name": item}
            for field in ("category", "quantity"):
                if input_data.get(field):
                    entry[field] = input_data[field]
            entries.append(entry)
        return tool_result(client.post(f"{base_path}/bulk", {"items": entries}))

    if action == "update":
        body = {}
        mapping = {"item": "name", "category": "category", "quantity": "quantity", "notes": "notes"}
        for source, destination in mapping.items():
            if input_data.get(source):
                body[destination] = input_data[source]
        if not body:
            return tool_error(
                "At least one field (item, category, quantity, notes) is required for update"
            )
        return tool_result(
            client.patch(f"{base_path}/{input_data['itemId']}", body)
        )

    if action == "delete":
        return tool_result(client.delete(f"{base_path}/{input_data['itemId']}"))

    if action == "delete_checked":
        if input_data.get("dryRun"):
            # Dry run: fetch and return checked items without deleting
            items_data = client.get(base_path)
            if isinstance(items_data, dict) and isinstance(items_data.get("items"), list):
                checked = [item for item in items_data["items"] if item.get("checked")]
                return tool_result({
                    "dryRun": True,
                    "items": checked,
                    "count": len(checked),
                })
            return tool_result({"dryRun": True, "items": [], "count": 0})
        return tool_result(client.delete(f"{base_path}/checked"))

    if action == "convert_to_tasks":
        # Normalize uncheckedOnly to the effective value
        unchecked_only = True if input_data.get("uncheckedOnly") is None else input_data.get("uncheckedOnly")
        body = {"uncheckedOnly": unchecked_only}
        if input_data.get("priority"):
            body["priority"] = input_data["priority"]
        if input_data.get("dryRun"):
            # Dry run: fetch items, filter using the same default, but don't convert
            items_data = client.get(base_path)
            if isinstance(items_data, dict) and isinstance(items_data.get("items"), list):
                items = items_data["items"]
                filtered = [item for item in items if not unchecked_only or not item.get("checked")]
                return tool_result({
                    "dryRun": True,
                    "items": filtered,
                    "count": len(filtered),
                })
            return tool_result({"dryRun": True, "items": [], "count": 0})
        return tool_result(client.post(f"{base_path}/convert-to-tasks", body))


def register_lists_tool(
    ctx: Any,
    client: MynApiClient,
    check_fn: Callable[[], bool],
) -> None:
    register_myn_tool(
        ctx,
        name="myn_lists",
        schema=LISTS_SCHEMA,
        handler=lambda **kwargs: execute_lists(client, **kwargs),
        check_fn=check_fn,
        description=(
            "Manage grocery and shopping lists. Actions: get, add, update, "
            "toggle, delete, delete_checked, bulk_add, convert_to_tasks."
        ),
        emoji="🛒",
    )
