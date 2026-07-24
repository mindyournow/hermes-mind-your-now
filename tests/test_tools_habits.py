import json
import sys
import types

import httpx
import pytest

from mind_your_now.client import MynApiClient
from mind_your_now.tools.habits import register_habits_tool


HABIT_ID = "11111111-1111-4111-8111-111111111111"
CHAIN_ID = "22222222-2222-4222-8222-222222222222"


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
    register_habits_tool(context, client, lambda: True)
    return context.registration["handler"]


@pytest.mark.parametrize(
    ("input_data", "method", "path"),
    [
        (
            {"action": "streaks", "habitId": HABIT_ID},
            "GET",
            f"/api/v2/unified-tasks/{HABIT_ID}/streak",
        ),
        (
            {"action": "skip", "habitId": HABIT_ID},
            "POST",
            f"/api/v2/unified-tasks/{HABIT_ID}/skip",
        ),
        ({"action": "chains"}, "GET", "/api/habits/chains"),
        (
            {"action": "chains", "chainId": CHAIN_ID},
            "GET",
            f"/api/habits/chains/{CHAIN_ID}/status",
        ),
        (
            {"action": "schedule"},
            "GET",
            "/api/v2/unified-tasks/schedule",
        ),
        ({"action": "reminders"}, "GET", "/api/habits/reminders"),
        (
            {"action": "reminders", "habitId": HABIT_ID},
            "GET",
            f"/api/habits/reminders/{HABIT_ID}",
        ),
        (
            {"action": "reminders", "habitId": HABIT_ID, "enableReminders": False},
            "PUT",
            f"/api/habits/reminders/{HABIT_ID}",
        ),
    ],
)
def test_actions_use_expected_methods_and_paths(input_data, method, path):
    observed = []
    payload = {"marker": input_data["action"]}

    def transport(request):
        observed.append((request.method, request.url.path))
        return httpx.Response(200, json=payload)

    result = json.loads(build_handler(transport)(**input_data))

    assert observed == [(method, path)]
    assert result == {"success": True, "data": payload}


def test_streak_history_and_schedule_days_use_params():
    observed = []

    def transport(request):
        observed.append((request.url.path, dict(request.url.params)))
        return httpx.Response(200, json={})

    handler = build_handler(transport)
    handler(action="streaks", habitId=HABIT_ID, includeHistory=True)
    handler(action="schedule", dateRange=14)

    assert observed == [
        (f"/api/v2/unified-tasks/{HABIT_ID}/streak", {"includeHistory": "true"}),
        ("/api/v2/unified-tasks/schedule", {"days": "14"}),
    ]


def test_reminder_update_preserves_false_and_time():
    observed_body = None

    def transport(request):
        nonlocal observed_body
        observed_body = json.loads(request.content)
        return httpx.Response(200, json={"habitId": HABIT_ID})

    handler = build_handler(transport)
    handler(
        action="reminders",
        habitId=HABIT_ID,
        enableReminders=False,
        reminderTime="08:30",
    )

    assert observed_body == {"enabled": False, "time": "08:30"}
