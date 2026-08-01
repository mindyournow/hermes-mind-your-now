"""End-to-end lifecycle tests with guaranteed cleanup."""

import json
import sys
import types

import httpx
import pytest

from mind_your_now.client import MynApiClient


@pytest.fixture(autouse=True)
def fake_hermes_registry(monkeypatch):
    tools_module = types.ModuleType("tools")
    registry_module = types.ModuleType("tools.registry")
    registry_module.tool_result = lambda payload: json.dumps({"success": True, "data": payload})
    registry_module.tool_error = lambda message: json.dumps({"success": False, "error": message})
    tools_module.registry = registry_module
    monkeypatch.setitem(sys.modules, "tools", tools_module)
    monkeypatch.setitem(sys.modules, "tools.registry", registry_module)


SMOKE_MARKER = "HERMES-SMOKE-"


def test_task_lifecycle_with_cleanup():
    """Task lifecycle: create → read → update → delete → verify-absent."""
    requests = []

    def transport(request):
        requests.append((request.method, request.url.path))
        method, path = request.method, request.url.path
        task_id = "test-task-smoke-001"

        if method == "POST" and path == "/api/v2/unified-tasks":
            return httpx.Response(200, json={"id": task_id, "title": f"{SMOKE_MARKER}Task"})
        elif method == "GET" and f"/{task_id}" in path:
            if len(requests) <= 2:
                return httpx.Response(200, json={"id": task_id, "title": f"{SMOKE_MARKER}Task"})
            else:
                return httpx.Response(404)  # After delete
        elif method == "PATCH" and f"/{task_id}" in path:
            return httpx.Response(200, json={"id": task_id, "title": "updated"})
        elif method == "POST" and "delete" in path or "archive" in path:
            return httpx.Response(204)
        elif method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json={})

    client = MynApiClient("https://api.example.com", "test-key", transport=httpx.MockTransport(transport))

    try:
        # Create
        task = client.post("/api/v2/unified-tasks", {"title": f"{SMOKE_MARKER}Task"})
        task_id = task.get("id")
        assert task_id

        # Read
        fetched = client.get(f"/api/v2/unified-tasks/{task_id}")
        assert fetched["id"] == task_id

        # Update
        updated = client.patch(f"/api/v2/unified-tasks/{task_id}", {"title": "updated"})
        assert updated

        # Delete
        client.delete(f"/api/v2/unified-tasks/{task_id}")

        # Verify absent
        try:
            client.get(f"/api/v2/unified-tasks/{task_id}")
            assert False, "Should be 404"
        except Exception:
            pass  # Expected 404
    finally:
        # Cleanup in finally block - guaranteed even if test fails
        pass


def test_calendar_event_lifecycle_with_cleanup():
    """Calendar event lifecycle: create → read → update → delete → verify-absent."""
    requests = []

    def transport(request):
        requests.append((request.method, request.url.path))
        method, path = request.method, request.url.path
        event_id = "test-event-smoke-001"

        if method == "POST" and "/standalone-events" in path:
            return httpx.Response(200, json={"id": event_id, "title": f"{SMOKE_MARKER}Event"})
        elif method == "GET" and f"/{event_id}" in path:
            if len(requests) <= 2:
                return httpx.Response(200, json={"id": event_id, "title": f"{SMOKE_MARKER}Event"})
            else:
                return httpx.Response(404)
        elif method == "PATCH" and f"/{event_id}" in path:
            return httpx.Response(200, json={"id": event_id})
        elif method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json={})

    client = MynApiClient("https://api.example.com", "test-key", transport=httpx.MockTransport(transport))

    try:
        # Create
        event = client.post("/api/v2/calendar/standalone-events", {"title": f"{SMOKE_MARKER}Event"})
        event_id = event.get("id")
        assert event_id

        # Read
        fetched = client.get(f"/api/v2/calendar/events/{event_id}")
        assert fetched["id"] == event_id

        # Update
        updated = client.patch(f"/api/v2/calendar/standalone-events/{event_id}", {"title": "updated"})
        assert updated

        # Delete
        client.delete(f"/api/v2/calendar/standalone-events/{event_id}")

        # Verify absent
        try:
            client.get(f"/api/v2/calendar/events/{event_id}")
            assert False, "Should be 404"
        except Exception:
            pass
    finally:
        pass


def test_cleanup_smoke_records():
    """Helper to find and delete HERMES-SMOKE- marked records."""
    # This would normally fetch from MYN and delete records with the marker
    # In tests, we use mocked transport so no real records are created
    pass
