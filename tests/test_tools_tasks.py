import json
import sys
import types

import httpx
import pytest

from mind_your_now.client import MynApiClient
from mind_your_now.tools.tasks import (
    ALLOWED_UPDATE_FIELDS,
    register_tasks_tool,
)


TASK_ID = "11111111-1111-4111-8111-111111111111"
EXPECTED_ALLOWED_UPDATE_FIELDS = {
    "title",
    "description",
    "priority",
    "status",
    "startDate",
    "endDate",
    "duration",
    "projectId",
    "recurrenceRule",
    "isAutoScheduled",
    "autoScheduleEnabled",
    "calendarId",
    "location",
    "notes",
    "tags",
    "estimatedMinutes",
    "actualMinutes",
    "completedAt",
    "archivedAt",
    "taskType",
    "assignedTo",
    "scheduledAt",
    "dueDate",
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


def build_handler(transport):
    client = MynApiClient(
        "https://api.example.com",
        "myn-key",
        transport=httpx.MockTransport(transport),
    )
    context = Context()
    register_tasks_tool(context, client, lambda: True)
    return context.registration["handler"]


@pytest.mark.parametrize(
    ("input_data", "expected_method", "expected_path"),
    [
        ({"action": "list"}, "GET", "/api/v2/unified-tasks"),
        (
            {"action": "get", "taskId": TASK_ID},
            "GET",
            f"/api/v2/unified-tasks/{TASK_ID}",
        ),
        (
            {
                "action": "create",
                "title": "Plan week",
                "priority": "OPPORTUNITY_NOW",
                "taskType": "TASK",
                "startDate": "2026-07-25",
            },
            "POST",
            "/api/v2/unified-tasks",
        ),
        (
            {"action": "update", "taskId": TASK_ID, "updates": {"title": "New"}},
            "PATCH",
            f"/api/v2/unified-tasks/{TASK_ID}",
        ),
        (
            {"action": "complete", "taskId": TASK_ID},
            "POST",
            f"/api/v2/unified-tasks/{TASK_ID}/complete",
        ),
        (
            {"action": "archive", "taskId": TASK_ID},
            "POST",
            f"/api/v2/unified-tasks/{TASK_ID}/archive",
        ),
        (
            {"action": "search", "query": "weekly plan"},
            "GET",
            "/api/v2/search",
        ),
    ],
)
def test_each_action_uses_expected_method_and_path(
    input_data, expected_method, expected_path
):
    observed = []

    def transport(request):
        observed.append((request.method, request.url.path))
        return httpx.Response(200, json={"ok": True})

    result = json.loads(build_handler(transport)(**input_data))

    assert observed == [(expected_method, expected_path)]
    assert result["success"] is True


def test_update_filters_unknown_fields_and_reports_dropped_fields():
    observed_body = None

    def transport(request):
        nonlocal observed_body
        observed_body = json.loads(request.content)
        return httpx.Response(200, json={"id": TASK_ID, "title": "Allowed"})

    handler = build_handler(transport)
    result = json.loads(
        handler(
            action="update",
            taskId=TASK_ID,
            updates={"title": "Allowed", "ownerId": "attacker-controlled"},
        )
    )

    assert observed_body == {"title": "Allowed"}
    assert result["data"]["droppedFields"] == ["ownerId"]
    assert "ownerId" not in result["data"]["data"]


def test_update_reports_dropped_fields_when_none_are_allowed():
    handler = build_handler(
        lambda _request: pytest.fail("request must not be sent")
    )

    result = json.loads(
        handler(
            action="update",
            taskId=TASK_ID,
            updates={"ownerId": "x", "householdId": "y"},
        )
    )

    assert result["success"] is False
    assert "ownerId, householdId" in result["error"]


def test_allowed_update_fields_match_typescript_source():
    assert ALLOWED_UPDATE_FIELDS == EXPECTED_ALLOWED_UPDATE_FIELDS


def test_create_defaults_to_auto_scheduled():
    observed_body = None

    def transport(request):
        nonlocal observed_body
        observed_body = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    handler = build_handler(transport)
    handler(
        action="create",
        title="Plan week",
        priority="OPPORTUNITY_NOW",
        taskType="TASK",
        startDate="2026-07-25",
    )

    assert observed_body["isAutoScheduled"] is True
