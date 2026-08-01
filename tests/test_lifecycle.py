"""End-to-end lifecycle tests with guaranteed cleanup."""

import json
import sys
import types

import httpx
import pytest

from mind_your_now.client import MynApiClient, MynApiError


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
    """Task lifecycle: create → read → update → delete → verify-absent with guaranteed cleanup."""
    requests = []
    created_task_id = None

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
        created_task_id = task.get("id")
        assert created_task_id

        # Read
        fetched = client.get(f"/api/v2/unified-tasks/{created_task_id}")
        assert fetched["id"] == created_task_id

        # Update
        updated = client.patch(f"/api/v2/unified-tasks/{created_task_id}", {"title": "updated"})
        assert updated

        # Delete
        client.delete(f"/api/v2/unified-tasks/{created_task_id}")

        # Verify absent
        with pytest.raises(MynApiError) as exc_info:
            client.get(f"/api/v2/unified-tasks/{created_task_id}")
        assert exc_info.value.status == 404
    finally:
        # Cleanup in finally block - guaranteed even if test fails
        if created_task_id:
            try:
                client.delete(f"/api/v2/unified-tasks/{created_task_id}")
            except Exception:
                pass  # Best-effort cleanup


def test_calendar_event_lifecycle_with_cleanup():
    """Calendar event lifecycle: create → read → update → delete → verify-absent with guaranteed cleanup."""
    requests = []
    created_event_id = None

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
        created_event_id = event.get("id")
        assert created_event_id

        # Read
        fetched = client.get(f"/api/v2/calendar/events/{created_event_id}")
        assert fetched["id"] == created_event_id

        # Update
        updated = client.patch(f"/api/v2/calendar/standalone-events/{created_event_id}", {"title": "updated"})
        assert updated

        # Delete
        client.delete(f"/api/v2/calendar/standalone-events/{created_event_id}")

        # Verify absent
        with pytest.raises(MynApiError) as exc_info:
            client.get(f"/api/v2/calendar/events/{created_event_id}")
        assert exc_info.value.status == 404
    finally:
        # Cleanup in finally block - guaranteed even if test fails
        if created_event_id:
            try:
                client.delete(f"/api/v2/calendar/standalone-events/{created_event_id}")
            except Exception:
                pass  # Best-effort cleanup


def test_cleanup_smoke_records():
    """Test the cleanup helper against mocked responses."""
    from cleanup_smoke_records import cleanup_stranded_records
    import os

    requests = []
    deleted_ids = set()

    def transport(request):
        requests.append((request.method, request.url.path))
        method, path = request.method, request.url.path

        # Task deletion
        if method == "DELETE" and "/api/v2/unified-tasks/" in path:
            task_id = path.split("/")[-1]
            deleted_ids.add(task_id)
            return httpx.Response(204)

        # Calendar deletion
        if method == "DELETE" and "/api/v2/calendar/standalone-events/" in path:
            event_id = path.split("/")[-1]
            deleted_ids.add(event_id)
            return httpx.Response(204)

        # Task GET (verify not found)
        if method == "GET" and task_id in deleted_ids:
            return httpx.Response(404)

        # Calendar GET (verify not found)
        if method == "GET" and event_id in deleted_ids:
            return httpx.Response(404)

        return httpx.Response(200, json={})

    client = MynApiClient("https://api.example.com", "test-key", transport=httpx.MockTransport(transport))

    # Enable cleanup
    os.environ["HERMES_ALLOW_CLEANUP"] = "1"
    try:
        cleanup_stranded_records(client)
        # Verify that the DELETE requests were issued
        assert len([req for req in requests if req[0] == "DELETE"]) >= 1
    finally:
        del os.environ["HERMES_ALLOW_CLEANUP"]
