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

    # For update/complete/archive, guarded_write adds a GET before the write
    if expected_method in ("PATCH", "POST") and "/unified-tasks/" in expected_path:
        # Guarded write: GET then PATCH/POST
        assert len(observed) == 2
        assert observed[0][0] == "GET"
        assert observed[0][1] == expected_path.replace("/complete", "").replace("/archive", "")
        assert observed[1] == (expected_method, expected_path)
    else:
        assert observed == [(expected_method, expected_path)]
    assert result["success"] is True


def test_list_sends_limit_and_unwraps_tasks_envelope():
    observed_params = None
    tasks = [{"id": TASK_ID, "title": "Plan week"}]

    def transport(request):
        nonlocal observed_params
        observed_params = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "tasks": tasks,
                "total": 1,
                "limit": 7,
                "offset": 0,
                "hasMore": False,
            },
        )

    result = json.loads(build_handler(transport)(action="list", limit=7))

    assert observed_params == {"limit": "200", "offset": "0"}
    assert result["data"] == {"tasks": tasks}


def test_list_limit_stops_after_first_sufficient_server_page():
    requests = 0

    def transport(_request):
        nonlocal requests
        requests += 1
        return httpx.Response(
            200,
            json={
                "tasks": [
                    {"id": f"task-{index}", "priority": "CRITICAL"}
                    for index in range(200)
                ],
                "hasMore": True,
                "snapshot": "stable-generation",
            },
        )

    result = json.loads(
        build_handler(transport)(action="list", priority="CRITICAL", limit=20)
    )

    assert requests == 1
    assert len(result["data"]["tasks"]) == 20
    assert result["data"]["_truncated"] is True
    assert result["data"]["_collectionComplete"] is False
    assert "_totalCount" not in result["data"]


def test_update_filters_unknown_fields_and_reports_dropped_fields():
    observed_requests = []

    def transport(request):
        if request.method == "PATCH":
            observed_requests.append(json.loads(request.content))
        return httpx.Response(200, json={"id": TASK_ID, "title": "Allowed"})

    handler = build_handler(transport)
    result = json.loads(
        handler(
            action="update",
            taskId=TASK_ID,
            updates={"title": "Allowed", "ownerId": "attacker-controlled"},
        )
    )

    assert observed_requests == [{"title": "Allowed"}]
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


def test_update_uses_guarded_write_with_state_hash():
    """update routes through guarded_write, which sends GET then PATCH with state hash."""
    requests_log = []

    def transport(request):
        requests_log.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"id": TASK_ID, "stateHash": "hash-v1"})
        return httpx.Response(200, json={"id": TASK_ID, "title": "Updated"})

    handler = build_handler(transport)
    json.loads(handler(action="update", taskId=TASK_ID, updates={"title": "Updated"}))

    # Should have GET then PATCH
    assert len(requests_log) >= 2
    get_req, patch_req = requests_log[0], requests_log[1]
    assert get_req[0] == "GET"
    assert f"/unified-tasks/{TASK_ID}" in get_req[1]
    assert patch_req[0] == "PATCH"
    assert f"/unified-tasks/{TASK_ID}" in patch_req[1]


def test_complete_uses_guarded_write_with_state_hash():
    """complete routes through guarded_write, which sends GET then POST with state hash."""
    requests_log = []

    def transport(request):
        requests_log.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"id": TASK_ID, "stateHash": "hash-v1"})
        return httpx.Response(200, json={"id": TASK_ID, "status": "COMPLETED"})

    handler = build_handler(transport)
    json.loads(handler(action="complete", taskId=TASK_ID))

    # Should have GET then POST
    assert len(requests_log) >= 2
    get_req, post_req = requests_log[0], requests_log[1]
    assert get_req[0] == "GET"
    assert f"/unified-tasks/{TASK_ID}" in get_req[1]
    assert post_req[0] == "POST"
    assert f"/unified-tasks/{TASK_ID}/complete" in post_req[1]


def test_archive_uses_guarded_write_with_state_hash():
    """archive routes through guarded_write, which sends GET then POST with state hash."""
    requests_log = []

    def transport(request):
        requests_log.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"id": TASK_ID, "stateHash": "hash-v1"})
        return httpx.Response(200, json={"id": TASK_ID, "status": "ARCHIVED"})

    handler = build_handler(transport)
    json.loads(handler(action="archive", taskId=TASK_ID))

    # Should have GET then POST
    assert len(requests_log) >= 2
    get_req, post_req = requests_log[0], requests_log[1]
    assert get_req[0] == "GET"
    assert f"/unified-tasks/{TASK_ID}" in get_req[1]
    assert post_req[0] == "POST"
    assert f"/unified-tasks/{TASK_ID}/archive" in post_req[1]


@pytest.mark.parametrize(
    ("filters", "expected_ids"),
    [
        ({"startDate": "2026-08-03"}, ["spans", "future"]),
        ({"endDate": "2026-08-02"}, ["spans", "end-only"]),
        (
            {"startDate": "2026-08-02", "endDate": "2026-08-04"},
            ["spans", "end-only"],
        ),
    ],
)
def test_list_date_filters_use_inclusive_task_interval_overlap(filters, expected_ids):
    observed_params = None
    tasks = [
        {"id": "spans", "startDate": "2026-08-01T10:00:00Z", "endDate": "2026-08-03T11:00:00Z"},
        {"id": "future", "startDate": "2026-08-05", "endDate": None},
        {"id": "end-only", "startDate": None, "endDate": "2026-08-02"},
        {"id": "undated", "startDate": None, "endDate": None},
    ]

    def transport(request):
        nonlocal observed_params
        observed_params = dict(request.url.params)
        return httpx.Response(200, json={"tasks": tasks})

    result = json.loads(build_handler(transport)(action="list", **filters))

    assert observed_params == {**filters, "limit": "200", "offset": "0"}
    assert [task["id"] for task in result["data"]["tasks"]] == expected_ids


def test_list_date_filter_excludes_undated_tasks_but_keeps_single_date_tasks():
    tasks = [
        {"id": "start-only", "startDate": "2026-08-03", "endDate": None},
        {"id": "end-only", "startDate": None, "endDate": "2026-08-03"},
        {"id": "undated", "startDate": None, "endDate": None},
    ]

    result = json.loads(
        build_handler(lambda _request: httpx.Response(200, json={"tasks": tasks}))(
            action="list",
            startDate="2026-08-03",
            endDate="2026-08-03",
        )
    )

    assert [task["id"] for task in result["data"]["tasks"]] == [
        "start-only",
        "end-only",
    ]


def test_list_filters_and_offsets_across_all_server_pages():
    observed_requests = []
    snapshot = "stable-generation"

    def transport(request):
        server_offset = int(request.url.params["offset"])
        observed_requests.append(dict(request.url.params))
        count = 200 if server_offset == 0 else 50
        return httpx.Response(
            200,
            json={
                "tasks": [
                    {
                        "id": f"task-{index}",
                        "priority": "CRITICAL",
                        "startDate": "2026-08-01",
                    }
                    for index in range(server_offset, server_offset + count)
                ],
                "hasMore": server_offset == 0,
                "snapshot": snapshot,
            },
        )

    result = json.loads(
        build_handler(transport)(
            action="list",
            priority="CRITICAL",
            offset=205,
            limit=10,
        )
    )

    assert observed_requests == [
        {"priority": "CRITICAL", "limit": "200", "offset": "0"},
        {
            "priority": "CRITICAL",
            "limit": "200",
            "offset": "200",
            "snapshot": snapshot,
        },
    ]
    assert [task["id"] for task in result["data"]["tasks"]] == [
        f"task-{index}" for index in range(205, 215)
    ]
    assert result["data"]["_totalCount"] == 250


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("limit", 1.5, "limit must be a positive integer"),
        ("limit", 0, "limit must be a positive integer"),
        ("offset", -1, "offset must be a non-negative integer"),
        ("offset", 1.5, "offset must be a non-negative integer"),
    ],
)
def test_list_rejects_invalid_pagination_before_request(field, value, message):
    result = json.loads(
        build_handler(lambda _request: pytest.fail("invalid pagination must not fetch"))(
            action="list",
            **{field: value},
        )
    )

    assert result["success"] is False
    assert result["error"] == message
