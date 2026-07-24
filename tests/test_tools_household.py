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
