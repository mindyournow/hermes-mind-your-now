import json
import sys
import types

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
