import json
import sys
import types

import httpx
import pytest

from mind_your_now.client import MynApiClient
from mind_your_now.tools.timers import register_timers_tool


TIMER_ID = "timer-1"
PROVENANCE = {
    "source_agent_name": "Hermes/hermes-eltmon",
    "source_channel": "telegram:eltmon",
}


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
    register_timers_tool(context, client, lambda: True, PROVENANCE)
    return context.registration["handler"]


@pytest.mark.parametrize(
    ("input_data", "method", "path"),
    [
        (
            {"action": "create_countdown", "durationMinutes": 2},
            "POST",
            "/api/v2/timers/countdown",
        ),
        (
            {"action": "create_alarm", "alarmTime": "2026-07-25T15:00:00Z"},
            "POST",
            "/api/v2/timers/alarm",
        ),
        ({"action": "list"}, "GET", "/api/v2/timers"),
        (
            {"action": "cancel", "timerId": TIMER_ID},
            "POST",
            f"/api/v2/timers/{TIMER_ID}/cancel",
        ),
        (
            {"action": "snooze", "timerId": TIMER_ID},
            "POST",
            f"/api/v2/timers/{TIMER_ID}/snooze",
        ),
        ({"action": "pomodoro"}, "POST", "/api/v2/timers/countdown"),
    ],
)
def test_actions_use_expected_methods_and_paths(input_data, method, path):
    observed = []
    payload = {"marker": input_data["action"]}

    def transport(request):
        observed.append((request.method, request.url.path))
        return httpx.Response(200, json=payload)

    result = json.loads(build_handler(transport)(**input_data))

    # For cancel/snooze, guarded_write adds a GET before the write
    if method == "POST" and "cancel" in path:
        # Guarded write: GET then POST
        assert len(observed) == 2
        assert observed[0][0] == "GET"
        assert f"/timers/" in observed[0][1]
        assert observed[1] == (method, path)
    elif method == "POST" and "snooze" in path:
        # Guarded write: GET then POST
        assert len(observed) == 2
        assert observed[0][0] == "GET"
        assert f"/timers/" in observed[0][1]
        assert observed[1] == (method, path)
    else:
        assert observed == [(method, path)]
    assert result == {"success": True, "data": payload}


@pytest.mark.parametrize(
    ("input_data", "path"),
    [
        (
            {"action": "create_alarm", "alarmTime": "2026-07-25T15:00:00Z"},
            "/api/v2/timers/alarm",
        ),
        (
            {"action": "create_countdown", "durationMinutes": 2},
            "/api/v2/timers/countdown",
        ),
    ],
)
def test_alarm_and_countdown_include_provenance(input_data, path):
    observed = {}

    def transport(request):
        observed["path"] = request.url.path
        observed["body"] = json.loads(request.content)
        return httpx.Response(200, json={"timerId": TIMER_ID})

    build_handler(transport)(**input_data)

    assert observed["path"] == path
    assert observed["body"]["sourceAgentName"] == "Hermes/hermes-eltmon"
    assert observed["body"]["sourceChannel"] == "telegram:eltmon"


def test_countdown_converts_minutes_and_pomodoro_defaults():
    bodies = []

    def transport(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"timerId": TIMER_ID})

    handler = build_handler(transport)
    handler(action="create_countdown", durationMinutes=2, label="Tea")
    handler(action="pomodoro")

    assert bodies[0]["durationSeconds"] == 120
    assert bodies[0]["name"] == "Tea"
    assert bodies[1] == {
        "name": "Pomodoro",
        "type": "POMODORO",
        "durationSeconds": 1500,
        "breakDuration": 300,
        "longBreakDuration": 900,
        "sessions": 4,
        "autoStart": False,
        "sourceAgentName": "Hermes/hermes-eltmon",
        "sourceChannel": "telegram:eltmon",
    }


def test_cancel_uses_guarded_write_with_state_hash():
    """cancel routes through guarded_write, which sends GET then POST with state hash."""
    requests_log = []

    def transport(request):
        requests_log.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"timerId": TIMER_ID, "stateHash": "hash-v1"})
        return httpx.Response(200, json={"timerId": TIMER_ID, "cancelled": True})

    handler = build_handler(transport)
    json.loads(handler(action="cancel", timerId=TIMER_ID))

    # Should have GET then POST
    assert len(requests_log) >= 2
    get_req, post_req = requests_log[0], requests_log[1]
    assert get_req[0] == "GET"
    assert f"/timers/{TIMER_ID}" in get_req[1]
    assert post_req[0] == "POST"
    assert f"/timers/{TIMER_ID}/cancel" in post_req[1]


def test_snooze_uses_guarded_write_with_state_hash():
    """snooze routes through guarded_write, which sends GET then POST with state hash."""
    requests_log = []

    def transport(request):
        requests_log.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"timerId": TIMER_ID, "stateHash": "hash-v1"})
        return httpx.Response(200, json={"timerId": TIMER_ID, "snoozedUntil": "2026-08-01T10:00:00Z"})

    handler = build_handler(transport)
    json.loads(handler(action="snooze", timerId=TIMER_ID, snoozeMinutes=10))

    # Should have GET then POST
    assert len(requests_log) >= 2
    get_req, post_req = requests_log[0], requests_log[1]
    assert get_req[0] == "GET"
    assert f"/timers/{TIMER_ID}" in get_req[1]
    assert post_req[0] == "POST"
    assert f"/timers/{TIMER_ID}/snooze" in post_req[1]
