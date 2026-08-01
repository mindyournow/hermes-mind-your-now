#!/usr/bin/env python3
"""Cleanup helper for stranded HERMES-SMOKE- test records."""

import os
import sys
from typing import Optional

from mind_your_now.client import MynApiClient


SMOKE_MARKER = "HERMES-SMOKE-"


def cleanup_stranded_records(client: MynApiClient) -> None:
    """Delete records carrying the HERMES-SMOKE- marker from production.

    This cleanup is only enabled if HERMES_ALLOW_CLEANUP env var is set.
    This prevents accidental deletion of records.
    """
    if not os.getenv("HERMES_ALLOW_CLEANUP"):
        print("Cleanup requires HERMES_ALLOW_CLEANUP=1 environment variable")
        return

    # Delete the two known stranded records
    # Task d14677dc-c1c1-487f-a3a7-92cb9aa7a02b (requires state-hash guarded write)
    task_id = "d14677dc-c1c1-487f-a3a7-92cb9aa7a02b"
    try:
        client.guarded_write(
            "DELETE",
            f"/api/v2/unified-tasks/{task_id}",
            get_path=f"/api/v2/unified-tasks/{task_id}",
        )
        # Verify deleted
        try:
            client.get(f"/api/v2/unified-tasks/{task_id}")
            print(f"ERROR: Task {task_id} still exists after delete")
        except Exception:
            print(f"OK: Task {task_id} deleted successfully")
    except Exception as e:
        print(f"Error deleting task {task_id}: {e}")

    # Calendar event abpsl6qtf043oph42bm8rvfk8g
    event_id = "abpsl6qtf043oph42bm8rvfk8g"
    try:
        client.delete(f"/api/v2/calendar/standalone-events/{event_id}")
        # Verify deleted
        try:
            client.get(f"/api/v2/calendar/events/{event_id}")
            print(f"ERROR: Calendar event {event_id} still exists after delete")
        except Exception:
            print(f"OK: Calendar event {event_id} deleted successfully")
    except Exception as e:
        print(f"Error deleting calendar event {event_id}: {e}")


if __name__ == "__main__":
    api_key = os.getenv("MYN_API_KEY")
    if not api_key:
        print("Error: MYN_API_KEY environment variable not set")
        sys.exit(1)

    base_url = os.getenv("MYN_BASE_URL", "https://api.mindyournow.com")
    client = MynApiClient(base_url, api_key)

    cleanup_stranded_records(client)
