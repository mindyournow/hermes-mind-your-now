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


def test_cleanup_smoke_records(monkeypatch):
    """Cleanup verifies markers, deletes both records, and confirms absence."""
    from cleanup_smoke_records import EVENT_ID, TASK_ID, cleanup_stranded_records

    requests = []
    task_deleted = False
    event_deleted = False

    def transport(request):
        nonlocal task_deleted, event_deleted
        requests.append((request.method, request.url.path, dict(request.url.params)))
        method, path = request.method, request.url.path

        if path == f"/api/v2/unified-tasks/{TASK_ID}":
            if method == "DELETE":
                task_deleted = True
                return httpx.Response(204)
            if task_deleted:
                return httpx.Response(404)
            return httpx.Response(
                200,
                json={
                    "id": TASK_ID,
                    "title": f"[SMOKE TEST] {SMOKE_MARKER}task",
                    "stateHash": "task-hash",
                },
            )

        if path == "/api/v2/calendar/events":
            events = [] if event_deleted else [
                {"id": EVENT_ID, "title": f"[SMOKE TEST] {SMOKE_MARKER}event"}
            ]
            return httpx.Response(200, json={"events": events})

        if path == f"/api/v2/calendar/standalone-events/{EVENT_ID}":
            event_deleted = True
            return httpx.Response(204)

        return httpx.Response(500)

    client = MynApiClient("https://api.example.com", "test-key", transport=httpx.MockTransport(transport))
    monkeypatch.setenv("HERMES_ALLOW_CLEANUP", "1")

    result = cleanup_stranded_records(client)

    assert result == {"task": "deleted", "calendarEvent": "deleted"}
    assert any(method == "DELETE" and path.endswith(TASK_ID) for method, path, _ in requests)
    assert any(method == "DELETE" and path.endswith(EVENT_ID) for method, path, _ in requests)


def test_cleanup_requires_exact_authorization_value(monkeypatch):
    from cleanup_smoke_records import cleanup_stranded_records

    monkeypatch.setenv("HERMES_ALLOW_CLEANUP", "0")
    client = MynApiClient(
        "https://api.example.com",
        "test-key",
        transport=httpx.MockTransport(lambda _request: pytest.fail("no request expected")),
    )

    with pytest.raises(RuntimeError, match="HERMES_ALLOW_CLEANUP=1"):
        cleanup_stranded_records(client)


def test_cleanup_refuses_record_without_smoke_marker(monkeypatch):
    from cleanup_smoke_records import TASK_ID, cleanup_stranded_records

    monkeypatch.setenv("HERMES_ALLOW_CLEANUP", "1")

    def transport(request):
        if request.method == "GET" and request.url.path.endswith(TASK_ID):
            return httpx.Response(200, json={"id": TASK_ID, "title": "Real user task"})
        return httpx.Response(500)

    client = MynApiClient("https://api.example.com", "test-key", transport=httpx.MockTransport(transport))

    with pytest.raises(RuntimeError, match="marker not found"):
        cleanup_stranded_records(client)


def test_cleanup_surfaces_non_404_verification_failure(monkeypatch):
    from cleanup_smoke_records import TASK_ID, cleanup_stranded_records

    monkeypatch.setenv("HERMES_ALLOW_CLEANUP", "1")
    task_deleted = False

    def transport(request):
        nonlocal task_deleted
        if request.url.path.endswith(TASK_ID):
            if request.method == "DELETE":
                task_deleted = True
                return httpx.Response(204)
            if task_deleted:
                return httpx.Response(500, text="verification failed")
            return httpx.Response(
                200,
                json={
                    "id": TASK_ID,
                    "title": f"{SMOKE_MARKER}task",
                    "stateHash": "task-hash",
                },
            )
        return httpx.Response(500)

    client = MynApiClient("https://api.example.com", "test-key", transport=httpx.MockTransport(transport))

    with pytest.raises(MynApiError) as exc_info:
        cleanup_stranded_records(client)

    assert exc_info.value.status == 500


def test_cleanup_refuses_calendar_record_changed_after_marker_validation(monkeypatch):
    from cleanup_smoke_records import EVENT_ID, TASK_ID, cleanup_stranded_records

    monkeypatch.setenv("HERMES_ALLOW_CLEANUP", "1")
    calendar_reads = 0

    def transport(request):
        nonlocal calendar_reads
        if request.url.path.endswith(TASK_ID):
            return httpx.Response(404)
        if request.url.path == "/api/v2/calendar/events":
            calendar_reads += 1
            title = (
                f"{SMOKE_MARKER}event"
                if calendar_reads == 1
                else "Real user event"
            )
            return httpx.Response(200, json={"events": [{"id": EVENT_ID, "title": title}]})
        if request.method == "DELETE":
            pytest.fail("changed calendar event must not be deleted")
        return httpx.Response(500)

    client = MynApiClient(
        "https://api.example.com",
        "test-key",
        transport=httpx.MockTransport(transport),
    )

    with pytest.raises(RuntimeError, match="record changed during validation"):
        cleanup_stranded_records(client)
