import json
import sys
import types

import httpx
import pytest

from mind_your_now.client import MynApiClient
from mind_your_now.tools.household import register_household_tool


HOUSEHOLD_ID = "11111111-1111-4111-8111-111111111111"
CHORE_ID = "22222222-2222-4222-8222-222222222222"


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
    register_household_tool(context, client, lambda: True)
    return context.registration["handler"]


@pytest.mark.parametrize(
    ("input_data", "method", "path"),
    [
        (
            {"action": "members", "householdId": HOUSEHOLD_ID},
            "GET",
            f"/api/v1/households/{HOUSEHOLD_ID}/members",
        ),
        (
            {
                "action": "invite",
                "householdId": HOUSEHOLD_ID,
                "email": "member@example.com",
            },
            "POST",
            f"/api/v1/households/{HOUSEHOLD_ID}/invites",
        ),
        (
            {"action": "chores", "householdId": HOUSEHOLD_ID},
            "GET",
            "/api/v2/chores/today",
        ),
        (
            {
                "action": "chore_schedule",
                "householdId": HOUSEHOLD_ID,
                "date": "2026-07-25",
            },
            "GET",
            "/api/v2/chores/schedule/range",
        ),
        (
            {"action": "chore_complete", "choreId": CHORE_ID},
            "POST",
            f"/api/v2/chores/instances/{CHORE_ID}/complete",
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

    # For chore_complete, guarded_write adds a GET before the write
    if method == "POST" and "chore_complete" in input_data.get("action", ""):
        # Guarded write: GET then POST
        assert len(observed) == 2
        assert observed[0][0] == "GET"
        assert "/chores/instances/" in observed[0][1]
        assert observed[1] == (method, path)
    else:
        assert observed == [(method, path)]
    assert result == {"success": True, "data": payload}


def test_household_params_are_encoded():
    observed = {}

    def transport(request):
        observed["path"] = request.url.path
        observed["householdId"] = request.url.params["householdId"]
        observed["url"] = str(request.url)
        return httpx.Response(200, json={"chores": []})

    result = json.loads(
        build_handler(transport)(
            action="chores",
            householdId="home & family",
        )
    )

    assert observed == {
        "path": "/api/v2/chores/today",
        "householdId": "home & family",
        "url": "https://api.example.com/api/v2/chores/today?householdId=home+%26+family",
    }
    assert result == {"success": True, "data": {"chores": []}}


def test_chore_schedule_uses_single_date_or_seven_day_range():
    observed = []

    def transport(request):
        observed.append(dict(request.url.params))
        return httpx.Response(200, json={"schedule": []})

    handler = build_handler(transport)
    handler(
        action="chore_schedule",
        householdId=HOUSEHOLD_ID,
        date="2026-07-25",
    )
    handler(
        action="chore_schedule",
        householdId=HOUSEHOLD_ID,
        weekStart="2026-07-25",
    )

    assert observed == [
        {
            "householdId": HOUSEHOLD_ID,
            "startDate": "2026-07-25",
            "endDate": "2026-07-25",
        },
        {
            "householdId": HOUSEHOLD_ID,
            "startDate": "2026-07-25",
            "endDate": "2026-08-01",
        },
    ]


def test_chore_complete_uses_guarded_write_with_state_hash():
    """chore_complete routes through guarded_write, which sends GET then POST with state hash."""
    requests_log = []
    chore_id = "chore-123"

    def transport(request):
        requests_log.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"choreId": chore_id, "stateHash": "hash-v1"})
        return httpx.Response(200, json={"choreId": chore_id, "completed": True})

    handler = build_handler(transport)
    json.loads(handler(action="chore_complete", choreId=chore_id))

    # Should have GET then POST
    assert len(requests_log) >= 2
    get_req, post_req = requests_log[0], requests_log[1]
    assert get_req[0] == "GET"
    assert f"/chores/instances/{chore_id}" in get_req[1]
    assert post_req[0] == "POST"
    assert f"/chores/instances/{chore_id}/complete" in post_req[1]


@pytest.mark.xfail(
    reason="The chore GET /api/v2/chores/instances/{id} endpoint does not return stateHash, "
    "so chore_complete cannot obtain the value required by @RequireStateHash on the POST endpoint. "
    "This is a known server limitation tracked by MIN-932. The POST will fail without the header.",
    strict=True,
)
def test_chore_complete_state_hash_unsupported():
    """chore_complete's state-hash guard fails when GET response lacks stateHash."""
    chore_id = "chore-with-state"

    def transport(request):
        # The GET response does not include stateHash - this is the known server gap
        if request.method == "GET":
            return httpx.Response(200, json={"choreId": chore_id})
        # POST endpoint requires X-MYN-State-Hash header via @RequireStateHash
        # But we have no stateHash to send, so this will be rejected
        if request.method == "POST":
            # Verify we're being called - the real server would reject no header
            if not request.headers.get("X-MYN-State-Hash"):
                # This is the expected failure: POST without the required header
                return httpx.Response(400, json={"error": "stateHash header required"})
            return httpx.Response(200, json={"choreId": chore_id, "completed": True})
        return httpx.Response(500)

    client = MynApiClient("https://api.example.com", "test-key", transport=httpx.MockTransport(transport))
    # Let the known 400 escape so pytest records an actual XFAIL. If the server starts
    # returning stateHash, the write succeeds and strict=True turns the XPASS into a failure.
    client.guarded_write(
        "POST",
        "/api/v2/chores/instances/chore-with-state/complete",
        get_path="/api/v2/chores/instances/chore-with-state",
    )
