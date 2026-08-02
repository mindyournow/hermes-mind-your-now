import json
import sys
import types

import httpx
import pytest

from mind_your_now.client import MynApiClient
from mind_your_now.tools.projects import register_projects_tool


PROJECT_ID = "11111111-1111-4111-8111-111111111111"
TASK_ID = "22222222-2222-4222-8222-222222222222"


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
    register_projects_tool(context, client, lambda: True)
    return context.registration["handler"]


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
            {"action": "create", "name": "Launch"},
            "POST",
            "/api/project/create",
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


def test_list_uses_encoded_flags_and_create_maps_parent_id():
    observed = []

    def transport(request):
        observed.append(
            {
                "path": request.url.path,
                "params": dict(request.url.params),
                "body": json.loads(request.content) if request.content else None,
            }
        )
        return httpx.Response(200, json={})

    handler = build_handler(transport)
    handler(action="list", includeArchived=True, includeStats=True)
    handler(
        action="create",
        name="Launch",
        parentProjectId=PROJECT_ID,
        color="#112233",
    )

    assert observed == [
        {
            "path": "/api/project/defaults",
            "params": {
                "limit": "50",
                "includeArchived": "true",
                "includeStats": "true",
            },
            "body": None,
        },
        {
            "path": "/api/project/create",
            "params": {},
            "body": {"name": "Launch", "parentId": PROJECT_ID, "color": "#112233"},
        },
    ]
