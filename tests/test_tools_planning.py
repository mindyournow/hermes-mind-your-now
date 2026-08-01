import json
import sys
import types
from datetime import date, timedelta

import httpx
import pytest

from mind_your_now.client import MynApiClient
from mind_your_now.tools.planning import register_planning_tool


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
    register_planning_tool(context, client, lambda: True)
    return context.registration["handler"]


@pytest.mark.parametrize(
    ("input_data", "method", "path"),
    [
        ({"action": "plan"}, "GET", "/planning/plan"),
        ({"action": "schedule_all"}, "POST", "/planning/scheduleAll"),
        ({"action": "reschedule"}, "POST", "/planning/kickTheCan"),
    ],
)
def test_actions_use_expected_methods_and_paths(input_data, method, path):
    observed = []

    def transport(request):
        observed.append((request.method, request.url.path))
        return httpx.Response(200, json="planned")

    result = json.loads(build_handler(transport)(**input_data))

    assert observed == [(method, path)]
    expected = "planned" if input_data["action"] == "reschedule" else {"result": "planned"}
    assert result == {"success": True, "data": expected}


def test_reschedule_passes_rebalance_via_params():
    observed = []

    def transport(request):
        observed.append(dict(request.url.params))
        return httpx.Response(200, json={"ok": True})

    handler = build_handler(transport)
    handler(action="reschedule", spreadOverDays=1)
    handler(action="reschedule", spreadOverDays=3)

    assert observed == [{"rebalance": "false"}, {"rebalance": "true"}]


def test_schema_advertises_implemented_read_only_dry_run():
    from mind_your_now.tools.planning import PLANNING_SCHEMA

    assert PLANNING_SCHEMA["properties"]["dryRun"]["type"] == "boolean"
    assert (
        "engine decisions"
        in PLANNING_SCHEMA["properties"]["dryRun"]["description"].lower()
    )


@pytest.mark.parametrize(
    ("action", "spread_over_days", "expected_ids"),
    [
        ("schedule_all", 1, ["eligible", "habit"]),
        ("reschedule", 1, ["eligible"]),
        ("reschedule", 3, ["eligible", "future"]),
    ],
)
def test_dry_run_returns_candidate_set_without_mutation(
    action, spread_over_days, expected_ids
):
    observed = []
    today = date.today()
    tasks = [
        {
            "id": "eligible",
            "title": "Eligible",
            "taskType": "TASK",
            "priority": "CRITICAL",
            "startDate": today.isoformat(),
            "isCompleted": False,
            "isAutoScheduled": False,
        },
        {
            "id": "future",
            "title": "Future",
            "taskType": "TASK",
            "priority": "CRITICAL",
            "startDate": (today + timedelta(days=2)).isoformat(),
            "isCompleted": False,
            "isAutoScheduled": False,
        },
        {
            "id": "completed",
            "title": "Completed",
            "taskType": "TASK",
            "priority": "CRITICAL",
            "startDate": today.isoformat(),
            "isCompleted": True,
            "isAutoScheduled": False,
        },
        {
            "id": "habit",
            "title": "Habit",
            "taskType": "HABIT",
            "priority": "CRITICAL",
            "startDate": today.isoformat(),
            "isCompleted": False,
            "isAutoScheduled": False,
        },
    ]

    def transport(request):
        observed.append((request.method, request.url.path, dict(request.url.params)))
        assert request.method == "GET"
        return httpx.Response(200, json={"tasks": tasks})

    result = json.loads(
        build_handler(transport)(
            action=action,
            spreadOverDays=spread_over_days,
            dryRun=True,
        )
    )

    assert observed == [
        ("GET", "/api/v2/unified-tasks", {"page": "0", "size": "200"})
    ]
    assert result["success"] is True
    assert [task["id"] for task in result["data"]["tasks"]] == expected_ids
    assert result["data"]["count"] == len(expected_ids)
    assert result["data"]["dryRun"] is True
    assert result["data"]["engineDecisionsPreviewed"] is False
    assert "MIN-932" in result["data"]["message"]
