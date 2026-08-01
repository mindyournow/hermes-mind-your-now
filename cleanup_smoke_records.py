#!/usr/bin/env python3
"""Cleanup helper for the two stranded HERMES-SMOKE- production records."""

import os
import sys
from typing import Any

from mind_your_now.client import MynApiClient, MynApiError


SMOKE_MARKER = "HERMES-SMOKE-"
TASK_ID = "d14677dc-c1c1-487f-a3a7-92cb9aa7a02b"
EVENT_ID = "abpsl6qtf043oph42bm8rvfk8g"
EVENT_START = "2026-08-01"
EVENT_END = "2026-08-02"


def _require_smoke_marker(record: dict[str, Any], record_type: str, record_id: str) -> None:
    marker_fields = ("title", "name", "summary", "description")
    marker_text = " ".join(str(record.get(field, "")) for field in marker_fields)
    if SMOKE_MARKER not in marker_text:
        raise RuntimeError(
            f"Refusing to delete {record_type} {record_id}: {SMOKE_MARKER} marker not found"
        )


def _calendar_events(client: MynApiClient) -> list[dict[str, Any]]:
    data = client.get(
        "/api/v2/calendar/events",
        params={"start": EVENT_START, "end": EVENT_END, "limit": 1000},
    )
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("events"), list):
        return data["events"]
    raise RuntimeError("Calendar event listing returned an unexpected response shape")


def cleanup_stranded_records(client: MynApiClient) -> dict[str, str]:
    """Delete the two known smoke records after exact authorization and marker checks."""
    if os.getenv("HERMES_ALLOW_CLEANUP") != "1":
        raise RuntimeError("Cleanup requires HERMES_ALLOW_CLEANUP=1")

    results: dict[str, str] = {}
    task_path = f"/api/v2/unified-tasks/{TASK_ID}"

    try:
        task = client.get(task_path)
    except MynApiError as exc:
        if exc.status != 404:
            raise
        results["task"] = "already_absent"
    else:
        if not isinstance(task, dict):
            raise RuntimeError(f"Task {TASK_ID} returned an unexpected response shape")
        _require_smoke_marker(task, "task", TASK_ID)
        client.guarded_write("DELETE", task_path, get_path=task_path)
        try:
            client.get(task_path)
        except MynApiError as exc:
            if exc.status != 404:
                raise
            results["task"] = "deleted"
        else:
            raise RuntimeError(f"Task {TASK_ID} still exists after deletion")

    events = _calendar_events(client)
    event = next((item for item in events if str(item.get("id")) == EVENT_ID), None)
    if event is None:
        results["calendarEvent"] = "already_absent"
    else:
        _require_smoke_marker(event, "calendar event", EVENT_ID)
        client.delete(
            f"/api/v2/calendar/standalone-events/{EVENT_ID}",
            params={"calendarId": "primary"},
        )
        remaining = _calendar_events(client)
        if any(str(item.get("id")) == EVENT_ID for item in remaining):
            raise RuntimeError(f"Calendar event {EVENT_ID} still exists after deletion")
        results["calendarEvent"] = "deleted"

    return results


if __name__ == "__main__":
    api_key = os.getenv("MYN_API_KEY")
    if not api_key:
        print("Error: MYN_API_KEY environment variable not set")
        sys.exit(1)

    base_url = os.getenv("MYN_BASE_URL", "https://api.mindyournow.com")
    client = MynApiClient(base_url, api_key)

    try:
        cleanup_results = cleanup_stranded_records(client)
    except Exception as exc:  # noqa: BLE001
        print(f"Cleanup failed: {exc}")
        sys.exit(1)

    for record_type, status in cleanup_results.items():
        print(f"OK: {record_type} {status}")
