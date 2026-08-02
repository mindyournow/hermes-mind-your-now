import json
import sys
import types

import httpx
import pytest

from mind_your_now.client import MynApiClient
from mind_your_now.tools.projects import PROJECTS_SCHEMA, register_projects_tool


PROJECT_ID = "11111111-1111-4111-8111-111111111111"
TASK_ID = "22222222-2222-4222-8222-222222222222"
REMOVED_FIELDS = {
    "name",
    "description",
    "color",
    "icon",
    "parentProjectId",
    "includeArchived",
    "includeStats",
}


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


def build_registration(transport):
    client = MynApiClient(
        "https://api.example.com",
        "myn-key",
        transport=httpx.MockTransport(transport),
    )
    context = Context()
    register_projects_tool(context, client, lambda: True)
    return context.registration


def build_handler(transport):
    return build_registration(transport)["handler"]


@pytest.mark.parametrize(
    ("input_data", "method", "path"),
    [
        ({"action": "list"}, "GET", "/api/project/defaults"),
        (
            {"action": "get", "projectId": PROJECT_ID},
            "GET",
            f"/api/project/{PROJECT_ID}",
        ),
        (
            {
                "action": "move_task",
                "taskId": TASK_ID,
                "targetProjectId": PROJECT_ID,
            },
            "PUT",
            f"/api/project/{PROJECT_ID}/moveTaskToProject/{TASK_ID}",
        ),
    ],
)
def test_actions_use_expected_methods_and_paths(input_data, method, path):
    observed = []
    payload = {"marker": input_data["action"]}

    def transport(request):
        observed.append((request.method, request.url.path))
        response = (
            {
                "projects": [payload],
                "total": 1,
                "limit": 50,
                "offset": 0,
                "hasMore": False,
            }
            if input_data["action"] == "list"
            else payload
        )
        return httpx.Response(200, json=response)

    result = json.loads(build_handler(transport)(**input_data))

    assert observed == [(method, path)]
    expected_data = {"projects": [payload]} if input_data["action"] == "list" else payload
    assert result == {"success": True, "data": expected_data}


def test_schema_exposes_only_supported_actions_and_fields():
    properties = PROJECTS_SCHEMA["properties"]

    assert properties["action"]["enum"] == ["list", "get", "move_task"]
    assert REMOVED_FIELDS.isdisjoint(properties)
    assert set(properties) == {
        "action",
        "projectId",
        "taskId",
        "targetProjectId",
        "limit",
    }


def test_list_sends_requested_limit_to_api():
    observed_params = []

    def transport(request):
        observed_params.append(dict(request.url.params))
        return httpx.Response(200, json={"projects": []})

    result = json.loads(build_handler(transport)(action="list", limit=25))

    assert observed_params == [{"limit": "25"}]
    assert result == {"success": True, "data": {"projects": []}}


def test_create_is_rejected_without_calling_api():
    def transport(_request):
        raise AssertionError("create must not call the API")

    result = json.loads(build_handler(transport)(action="create", name="Launch"))

    assert result == {"success": False, "error": "Unknown action: create"}


def test_description_explains_fixed_collections_and_move_task():
    registration = build_registration(
        lambda _request: httpx.Response(200, json={"projects": []})
    )
    description = registration["description"]

    assert "fixed set of collections" in description
    assert "cannot be created, renamed, or deleted" in description
    assert "use move_task" in description
    assert "Actions: list, get, move_task" in description
