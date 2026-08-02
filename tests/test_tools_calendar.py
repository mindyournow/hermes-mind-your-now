import json
import sys
import types

import httpx
import pytest

from mind_your_now.client import MynApiClient
from mind_your_now.tools.calendar import register_calendar_tool


EVENT_ID = "event-1"


@pytest.fixture(autouse=True)
def fake_hermes_registry(monkeypatch):
    tools_module = types.ModuleType("tools")
    registry_module = types.ModuleType("tools.registry")
    registry_module.tool_result = lambda payload: json.dumps(
        {"success": True, "data": payload}, sort_keys=True
    )
    registry_module.tool_error = lambda message: json.dumps(
        {"success": False, "error": message}, sort_keys=True
    )
    tools_module.registry = registry_module
    monkeypatch.setitem(sys.modules, "tools", tools_module)
    monkeypatch.setitem(sys.modules, "tools.registry", registry_module)


class Context:
    def register_tool(self, **kwargs):
        self.registration = kwargs


def build_handler(transport):
    client = MynApiClient(
        "https://api.example.com",
        "myn-key",
        transport=httpx.MockTransport(transport),
    )
    context = Context()
    register_calendar_tool(context, client, lambda: True)
    return context.registration["handler"]


@pytest.mark.parametrize(
    ("input_data", "method", "path", "response"),
    [
        (
            {"action": "list_calendars"},
            "GET",
            "/api/v1/customers/calendars",
            {"calendars": []},
        ),
        (
            {"action": "list_events"},
            "GET",
            "/api/v2/calendar/events",
            {"events": [], "total": 0},
        ),
        (
            {"action": "get_event", "eventId": EVENT_ID},
            "GET",
            f"/api/v2/calendar/events/{EVENT_ID}",
            {"id": EVENT_ID, "title": "Review"},
        ),
        (
            {
                "action": "create_event",
                "title": "Review",
                "startTime": "2026-07-25T10:00:00",
                "endTime": "2026-07-25T10:30:00",
            },
            "POST",
            "/api/v2/calendar/standalone-events",
            {"id": EVENT_ID},
        ),
        (
            {"action": "update_event", "eventId": EVENT_ID, "newTitle": "New"},
            "PATCH",
            f"/api/v2/calendar/standalone-events/{EVENT_ID}",
            {"id": EVENT_ID},
        ),
        (
            {"action": "delete_event", "eventId": EVENT_ID},
            "DELETE",
            f"/api/v2/calendar/standalone-events/{EVENT_ID}",
            None,
        ),
        (
            {
                "action": "move_event",
                "eventId": EVENT_ID,
                "destinationCalendarId": "family",
            },
            "POST",
            f"/api/v2/calendar/standalone-events/{EVENT_ID}/move",
            {"id": EVENT_ID},
        ),
        (
            {"action": "meetings"},
            "GET",
            "/api/v2/calendar/events",
            {"events": [], "total": 0},
        ),
    ],
)
def test_each_action_uses_expected_method_and_path(
    input_data, method, path, response
):
    observed = []

    def transport(request):
        observed.append((request.method, request.url.path))
        if response is None:
            return httpx.Response(204)
        return httpx.Response(200, json=response)

    result = json.loads(build_handler(transport)(**input_data))

    assert observed == [(method, path)]
    assert result["success"] is True


def test_list_events_preserves_slimmed_response_format():
    payload = {
        "events": [
            {
                "id": EVENT_ID,
                "title": "Review",
                "description": "<p>Hello <strong>world</strong></p>",
                "attendees": [{"email": "person@example.com"}],
                "transparency": "opaque",
            }
        ],
        "total": 1,
    }
    handler = build_handler(lambda _request: httpx.Response(200, json=payload))

    result = json.loads(handler(action="list_events"))

    assert result["data"] == {
        "events": [
            {
                "id": EVENT_ID,
                "title": "Review",
                "description": "Hello world",
            }
        ],
        "total": 1,
    }


def test_update_and_move_use_encoded_query_params():
    observed = []

    def transport(request):
        observed.append((request.url.path, dict(request.url.params)))
        return httpx.Response(200, json={"id": EVENT_ID})

    handler = build_handler(transport)
    handler(
        action="update_event",
        eventId=EVENT_ID,
        newTitle="New",
        calendarId="family & shared",
    )
    handler(
        action="move_event",
        eventId=EVENT_ID,
        sourceCalendarId="primary & work",
        destinationCalendarId="family & shared",
    )

    assert observed == [
        (
            f"/api/v2/calendar/standalone-events/{EVENT_ID}",
            {"calendarId": "family & shared"},
        ),
        (
            f"/api/v2/calendar/standalone-events/{EVENT_ID}/move",
            {
                "sourceCalendarId": "primary & work",
                "destinationCalendarId": "family & shared",
            },
        ),
    ]


def test_create_normalizes_bare_times_with_start_date():
    observed_body = None

    def transport(request):
        nonlocal observed_body
        observed_body = json.loads(request.content)
        return httpx.Response(200, json={"id": EVENT_ID})

    handler = build_handler(transport)
    handler(
        action="create_event",
        title="Review",
        startDate="2026-07-25T00:00:00",
        startTime="10:00",
        endTime="10:30",
    )

    assert observed_body["startTime"] == "2026-07-25T10:00:00"
    assert observed_body["endTime"] == "2026-07-25T10:30:00"


def test_meetings_filters_events_without_attendees():
    payload = {
        "events": [
            {"id": "meeting", "title": "Meeting", "attendees": [{"email": "a@b.com"}]},
            {"id": "focus", "title": "Focus", "attendees": []},
        ],
        "total": 2,
    }
    handler = build_handler(lambda _request: httpx.Response(200, json=payload))

    result = json.loads(handler(action="meetings"))

    assert result["data"] == {
        "events": [{"id": "meeting", "title": "Meeting"}],
        "total": 1,
    }


def test_create_and_delete_use_standalone_events():
    """Verify create and delete both use /standalone-events endpoint."""
    requests_log = []
    event_id = "event-123"

    def transport(request):
        requests_log.append((request.method, request.url.path, request.url.params))
        if request.method == "POST":
            return httpx.Response(200, json={"eventId": event_id})
        elif request.method == "DELETE":
            return httpx.Response(200, json={"deleted": True})
        return httpx.Response(200, json={})

    handler = build_handler(transport)

    # Create event
    json.loads(handler(action="create_event", title="Test", startTime="2026-08-01T10:00:00Z", endTime="2026-08-01T11:00:00Z"))

    # Delete event
    json.loads(handler(action="delete_event", eventId=event_id))

    # Both should use /standalone-events
    assert len(requests_log) == 2
    create_req = requests_log[0]
    delete_req = requests_log[1]

    # Both should hit standalone-events endpoint
    assert "standalone-events" in create_req[1]
    assert "standalone-events" in delete_req[1]

    # Delete should pass calendarId param (create doesn't need it)
    assert delete_req[2]["calendarId"] == "primary"


def test_delete_event_uses_correct_endpoint():
    """delete_event sends DELETE to /standalone-events not /calendar/events."""
    requests_log = []
    event_id = "event-456"

    def transport(request):
        requests_log.append((request.method, request.url.path))
        return httpx.Response(200, json={"deleted": True})

    handler = build_handler(transport)
    handler(action="delete_event", eventId=event_id, calendarId="work-calendar")

    assert len(requests_log) == 1
    delete_req = requests_log[0]
    assert delete_req[0] == "DELETE"
    assert delete_req[1] == f"/api/v2/calendar/standalone-events/{event_id}"
