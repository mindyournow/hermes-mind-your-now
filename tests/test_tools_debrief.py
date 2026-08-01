import json
import sys
import types

import httpx
import pytest

from mind_your_now.client import MynApiClient
from mind_your_now.tools.debrief import register_debrief_tool


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
    register_debrief_tool(context, client, lambda: True)
    return context.registration["handler"]


@pytest.mark.parametrize(
    ("input_data", "expected_method", "expected_path"),
    [
        ({"action": "status"}, "GET", "/api/v2/debrief/status"),
        ({"action": "generate"}, "POST", "/api/v2/debrief/generate"),
        ({"action": "get"}, "GET", "/api/v2/debrief/current"),
        (
            {"action": "apply_correction", "correctionType": "TASK_COMPLETED"},
            "POST",
            "/api/v2/debrief/corrections/apply",
        ),
        (
            {"action": "complete_session"},
            "POST",
            "/api/v2/debrief/complete",
        ),
    ],
)
def test_each_action_uses_expected_method_and_path(
    input_data, expected_method, expected_path
):
    observed = []
    payload = {"marker": input_data["action"]}

    def transport(request):
        observed.append((request.method, request.url.path))
        return httpx.Response(200, json=payload)

    result = json.loads(build_handler(transport)(**input_data))

    assert observed == [(expected_method, expected_path)]
    assert result == {"success": True, "data": payload}


def test_get_returns_empty_current_as_data():
    def transport(_request):
        return httpx.Response(200, json={"current": None})

    result = json.loads(build_handler(transport)(action="get"))

    assert result == {"success": True, "data": {"current": None}}


def test_generate_defaults_to_daily_and_preserves_optional_fields():
    observed_body = None

    def transport(request):
        nonlocal observed_body
        observed_body = json.loads(request.content)
        return httpx.Response(200, json={"debriefId": "d1"})

    handler = build_handler(transport)
    handler(
        action="generate",
        context="Focus on deadlines",
        focusAreas=["tasks", "calendar"],
    )

    assert observed_body == {
        "type": "DAILY",
        "context": "Focus on deadlines",
        "focusAreas": ["tasks", "calendar"],
    }


def test_apply_correction_requires_type():
    handler = build_handler(
        lambda _request: pytest.fail("request must not be sent")
    )

    result = json.loads(handler(action="apply_correction"))

    assert result == {
        "success": False,
        "error": "correctionType is required for apply_correction action",
    }
