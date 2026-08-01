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
            "/api/v2/unified-tasks",
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
        response_data = {"tasks": []} if "/api/v2/unified-tasks" in request.url.path else {}
        return httpx.Response(200, json=response_data)

    handler = build_handler(transport)
    handler(action="streaks", habitId=HABIT_ID, includeHistory=True)
    handler(action="schedule", dateRange=14)

    assert observed == [
        (f"/api/v2/unified-tasks/{HABIT_ID}/streak", {"includeHistory": "true"}),
        ("/api/v2/unified-tasks", {"type": "HABIT", "days": "14"}),
    ]


def test_schedule_lists_habits():
    """schedule now lists habits instead of calling a schedule endpoint."""
    observed = []

    def transport(request):
        observed.append(request.url.path)
        return httpx.Response(200, json={"tasks": [
            {"id": HABIT_ID, "title": "Morning run", "taskType": "HABIT", "checked": False},
            {"id": "22222222-2222-4222-8222-222222222222", "title": "Read", "taskType": "HABIT", "checked": True},
        ]})

    handler = build_handler(transport)
    result = json.loads(handler(action="schedule"))

    assert observed == ["/api/v2/unified-tasks"]
    assert result["success"] is True
    assert len(result["data"]["tasks"]) == 2
