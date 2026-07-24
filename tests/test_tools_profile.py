import json
import sys
import types

import httpx
import pytest

from mind_your_now.client import MynApiClient
from mind_your_now.tools.profile import register_profile_tool


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
    register_profile_tool(context, client, lambda: True)
    return context.registration["handler"]


@pytest.mark.parametrize(
    ("input_data", "method", "path"),
    [
        ({"action": "get_info"}, "GET", "/api/v1/customers"),
        ({"action": "get_goals"}, "GET", "/api/v1/customers/goals"),
        (
            {"action": "update_goals", "goals": [{"title": "Ship plugin"}]},
            "PUT",
            "/api/v1/customers/goals",
        ),
        (
            {"action": "preferences", "preferenceKey": "theme-preference"},
            "GET",
            "/api/v1/customers/theme-preference",
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


def test_update_goals_formats_and_escapes_markdown():
    observed_body = None

    def transport(request):
        nonlocal observed_body
        observed_body = json.loads(request.content)
        return httpx.Response(200, json={"status": "ok"})

    handler = build_handler(transport)
    handler(
        action="update_goals",
        goals=[
            {
                "title": "Ship *plugin*",
                "status": "active",
                "priority": "high",
                "description": "Avoid [regressions]",
                "targetDate": "2026-08-01",
            }
        ],
    )

    assert observed_body == {
        "goalsAndAmbitions": (
            "- **Ship \\*plugin\\*** [active] (high priority)\n"
            "  Avoid \\[regressions\\]\n"
            "  Target: 2026\\-08\\-01"
        )
    }


def test_preferences_update_and_fetch_all():
    observed = []

    def transport(request):
        observed.append((request.method, request.url.path))
        return httpx.Response(200, json={"value": request.url.path})

    handler = build_handler(transport)
    update = json.loads(
        handler(
            action="preferences",
            preferenceKey="coaching-intensity",
            preferenceValue={"level": "direct"},
        )
    )
    all_preferences = json.loads(handler(action="preferences"))

    assert update["success"] is True
    assert observed == [
        ("PUT", "/api/v1/customers/coaching-intensity"),
        ("GET", "/api/v1/customers/notification-preferences"),
        ("GET", "/api/v1/customers/coaching-intensity"),
        ("GET", "/api/v1/customers/theme-preference"),
    ]
    assert set(all_preferences["data"]["preferences"]) == {
        "notification-preferences",
        "coaching-intensity",
        "theme-preference",
    }
