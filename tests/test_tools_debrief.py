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

    # For apply_correction/complete_session, guarded_write adds a GET before the write
    if expected_method == "POST" and "corrections" in expected_path:
        # Guarded write: GET then POST
        assert len(observed) == 2
        assert observed[0] == ("GET", "/api/v2/debrief/current")
        assert observed[1] == (expected_method, expected_path)
    elif expected_method == "POST" and "complete" in expected_path:
        # Guarded write: GET then POST
        assert len(observed) == 2
        assert observed[0] == ("GET", "/api/v2/debrief/current")
        assert observed[1] == (expected_method, expected_path)
    else:
        assert observed == [(expected_method, expected_path)]
    assert result == {"success": True, "data": payload}


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


def test_apply_correction_uses_guarded_write_with_state_hash():
    """apply_correction routes through guarded_write, which sends GET then POST with state hash."""
    requests_log = []

    def transport(request):
        requests_log.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"stateHash": "hash-v1"})
        return httpx.Response(200, json={"corrected": True})

    handler = build_handler(transport)
    json.loads(handler(action="apply_correction", correctionType="tone_adjustment"))

    # Should have GET then POST
    assert len(requests_log) >= 2
    get_req, post_req = requests_log[0], requests_log[1]
    assert get_req[0] == "GET"
    assert "/debrief/current" in get_req[1]
    assert post_req[0] == "POST"
    assert "/debrief/corrections/apply" in post_req[1]


def test_complete_session_uses_guarded_write_with_state_hash():
    """complete_session routes through guarded_write, which sends GET then POST with state hash."""
    requests_log = []

    def transport(request):
        requests_log.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"stateHash": "hash-v1"})
        return httpx.Response(200, json={"completed": True})

    handler = build_handler(transport)
    json.loads(handler(action="complete_session", sessionSummary="Good progress"))

    # Should have GET then POST
    assert len(requests_log) >= 2
    get_req, post_req = requests_log[0], requests_log[1]
    assert get_req[0] == "GET"
    assert "/debrief/current" in get_req[1]
    assert post_req[0] == "POST"
    assert "/debrief/complete" in post_req[1]
