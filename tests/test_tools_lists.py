import json
import sys
import types

import httpx
import pytest

from mind_your_now.client import MynApiClient
from mind_your_now.tools.lists import register_lists_tool


HOUSEHOLD_ID = "11111111-1111-4111-8111-111111111111"
ITEM_ID = "22222222-2222-4222-8222-222222222222"
BASE = f"/api/v1/households/{HOUSEHOLD_ID}/grocery-list"


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
    register_lists_tool(context, client, lambda: True)
    return context.registration["handler"]


@pytest.mark.parametrize(
    ("input_data", "methods_paths"),
    [
        (
            {"action": "get", "householdId": HOUSEHOLD_ID},
            [("GET", BASE)],
        ),
        (
            {"action": "add", "householdId": HOUSEHOLD_ID, "item": "Milk"},
            [("POST", BASE)],
        ),
        (
            {"action": "toggle", "householdId": HOUSEHOLD_ID, "itemId": ITEM_ID},
            [("GET", BASE), ("PATCH", f"{BASE}/{ITEM_ID}/toggle")],
        ),
        (
            {"action": "bulk_add", "householdId": HOUSEHOLD_ID, "items": ["Milk"]},
            [("POST", f"{BASE}/bulk")],
        ),
        (
            {
                "action": "update",
                "householdId": HOUSEHOLD_ID,
                "itemId": ITEM_ID,
                "item": "Oat milk",
            },
            [("PATCH", f"{BASE}/{ITEM_ID}")],
        ),
        (
            {"action": "delete", "householdId": HOUSEHOLD_ID, "itemId": ITEM_ID},
            [("DELETE", f"{BASE}/{ITEM_ID}")],
        ),
        (
            {"action": "delete_checked", "householdId": HOUSEHOLD_ID},
            [("DELETE", f"{BASE}/checked")],
        ),
        (
            {"action": "convert_to_tasks", "householdId": HOUSEHOLD_ID},
            [("POST", f"{BASE}/convert-to-tasks")],
        ),
    ],
)
def test_actions_use_expected_methods_and_paths(input_data, methods_paths):
    observed = []
    payload = {"marker": input_data["action"]}

    def transport(request):
        observed.append((request.method, request.url.path))
        return httpx.Response(200, json=payload)

    result = json.loads(build_handler(transport)(**input_data))

    assert observed == methods_paths
    assert result == {"success": True, "data": payload}


def test_resolves_current_household_when_id_omitted():
    observed = []

    def transport(request):
        observed.append(request.url.path)
        if request.url.path == "/api/v1/households/current":
            return httpx.Response(200, json={"id": HOUSEHOLD_ID})
        return httpx.Response(200, json={"items": []})

    result = json.loads(build_handler(transport)(action="get"))

    assert observed == ["/api/v1/households/current", BASE]
    assert result == {"success": True, "data": {"items": []}}


def test_bulk_add_and_convert_preserve_typescript_bodies():
    bodies = []

    def transport(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={})

    handler = build_handler(transport)
    handler(
        action="bulk_add",
        householdId=HOUSEHOLD_ID,
        items=["Milk", "Bread"],
        category="pantry",
        quantity="2",
    )
    handler(
        action="convert_to_tasks",
        householdId=HOUSEHOLD_ID,
        uncheckedOnly=False,
        priority="CRITICAL",
    )

    assert bodies == [
        {
            "items": [
                {"name": "Milk", "category": "pantry", "quantity": "2"},
                {"name": "Bread", "category": "pantry", "quantity": "2"},
            ]
        },
        {"uncheckedOnly": False, "priority": "CRITICAL"},
    ]
