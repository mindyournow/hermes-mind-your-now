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
    "reminderEnabled",
    "reminderTime",
    "parentTaskId",
    "position",
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

    assert observed_params == {
        "detail": "full",
        "limit": "200",
        "offset": "0",
    }
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


def test_update_accepts_reminder_fields_verbatim():
    observed_body = None

    def transport(request):
        nonlocal observed_body
        if request.method == "GET":
            return httpx.Response(200, json={"id": TASK_ID, "stateHash": "hash-v1"})
        observed_body = json.loads(request.content)
        return httpx.Response(200, json={"id": TASK_ID, **observed_body})

    result = json.loads(
        build_handler(transport)(
            action="update",
            taskId=TASK_ID,
            updates={"reminderEnabled": True, "reminderTime": "09:00"},
        )
    )

    assert observed_body == {"reminderEnabled": True, "reminderTime": "09:00"}
    assert result["success"] is True


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
    assert "reminderEnabled" in result["error"]
    assert "reminderTime" in result["error"]


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

    assert observed_params == {
        **filters,
        "detail": "full",
        "limit": "200",
        "offset": "0",
    }
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
        {
            "priority": "CRITICAL",
            "detail": "full",
            "limit": "200",
            "offset": "0",
        },
        {
            "priority": "CRITICAL",
            "detail": "full",
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


# ---- Task sharing ----

MEMBER_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
MEMBER_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def _recording_transport(observed, response=None, status=200):
    def transport(request):
        observed.append(
            (
                request.method,
                request.url.path,
                json.loads(request.content) if request.content else None,
            )
        )
        if response is None:
            return httpx.Response(status)
        return httpx.Response(status, json=response)

    return transport


def test_share_posts_member_ids_share_type_and_message():
    observed = []
    handler = build_handler(_recording_transport(observed, {"status": "PENDING"}))

    result = json.loads(
        handler(
            action="share",
            taskId=TASK_ID,
            memberIds=[MEMBER_A, MEMBER_B, MEMBER_A],
            shareType="view",
            message="Can you pick this up?",
        )
    )

    assert observed == [
        (
            "POST",
            f"/api/v2/unified-tasks/{TASK_ID}/share",
            {
                "member_ids": [MEMBER_A, MEMBER_B],
                "share_type": "VIEW",
                "message": "Can you pick this up?",
            },
        )
    ]
    assert result == {"success": True, "data": {"status": "PENDING"}}


def test_share_defaults_to_edit_like_the_app():
    observed = []
    handler = build_handler(_recording_transport(observed, {}))

    handler(action="share", taskId=TASK_ID, memberIds=[MEMBER_A])

    assert observed[0][2] == {"member_ids": [MEMBER_A], "share_type": "EDIT"}


@pytest.mark.parametrize(
    ("input_data", "message"),
    [
        ({"action": "share", "memberIds": [MEMBER_A]}, "taskId is required"),
        ({"action": "share", "taskId": TASK_ID}, "memberIds is required"),
        ({"action": "share", "taskId": TASK_ID, "memberIds": []}, "memberIds is required"),
        (
            {"action": "share", "taskId": TASK_ID, "memberIds": ["42"]},
            "memberIds entry must be a valid UUID",
        ),
        (
            {"action": "share", "taskId": TASK_ID, "memberIds": [MEMBER_A], "shareType": "OWN"},
            "shareType must be one of VIEW, EDIT, DELEGATE",
        ),
        ({"action": "revoke_share", "taskId": TASK_ID}, "memberId is required"),
        (
            {"action": "revoke_share", "taskId": TASK_ID, "memberId": "nope"},
            "memberId must be a valid UUID",
        ),
        ({"action": "respond_share", "taskId": TASK_ID}, "ACCEPT or DECLINE"),
        (
            {"action": "respond_share", "taskId": TASK_ID, "response": "MAYBE"},
            "ACCEPT or DECLINE",
        ),
        ({"action": "list_shares"}, "taskId is required"),
    ],
)
def test_share_actions_validate_before_calling_api(input_data, message):
    observed = []
    result = json.loads(build_handler(_recording_transport(observed, {}))(**input_data))

    assert observed == []
    assert result["success"] is False
    assert message in result["error"]


def test_list_shares_and_shared_with_me_use_get():
    observed = []
    handler = build_handler(_recording_transport(observed, []))

    handler(action="list_shares", taskId=TASK_ID)
    handler(action="shared_with_me")

    assert observed == [
        ("GET", f"/api/v2/unified-tasks/{TASK_ID}/shares", None),
        ("GET", "/api/v2/unified-tasks/shared-with-me", None),
    ]


def test_revoke_share_deletes_member_share_and_reports_success_on_204():
    observed = []
    handler = build_handler(_recording_transport(observed, None, status=204))

    result = json.loads(handler(action="revoke_share", taskId=TASK_ID, memberId=MEMBER_A))

    assert observed == [("DELETE", f"/api/v2/unified-tasks/{TASK_ID}/share/{MEMBER_A}", None)]
    assert result == {
        "success": True,
        "data": {"revoked": True, "taskId": TASK_ID, "memberId": MEMBER_A},
    }


def test_respond_share_accepts_with_note():
    observed = []
    handler = build_handler(_recording_transport(observed, {"status": "ACCEPTED"}))

    result = json.loads(
        handler(action="respond_share", taskId=TASK_ID, response="accept", note="On it")
    )

    assert observed == [
        (
            "POST",
            f"/api/v2/unified-tasks/{TASK_ID}/share/respond",
            {"response": "ACCEPT", "note": "On it"},
        )
    ]
    assert result == {"success": True, "data": {"status": "ACCEPTED"}}


def test_respond_share_decline_with_empty_body_reports_outcome():
    observed = []
    handler = build_handler(_recording_transport(observed, None))

    result = json.loads(handler(action="respond_share", taskId=TASK_ID, response="DECLINE"))

    assert result == {"success": True, "data": {"taskId": TASK_ID, "response": "DECLINE"}}


def test_share_api_error_is_surfaced():
    observed = []
    handler = build_handler(
        _recording_transport(
            observed, {"message": "Chores cannot be shared - they are household-specific"}, 400
        )
    )

    result = json.loads(handler(action="share", taskId=TASK_ID, memberIds=[MEMBER_A]))

    assert result["success"] is False
    assert "Chores cannot be shared" in result["error"]


PLAN_ID = "22222222-2222-4222-8222-222222222222"
STEP_A = "33333333-3333-4333-8333-333333333333"
STEP_B = "44444444-4444-4444-8444-444444444444"


def plan_body(step_ids):
    return {
        "planId": PLAN_ID,
        "title": "Bothell move",
        "stepsDone": 0,
        "stepsTotal": len(step_ids),
        "currentStep": {"id": step_ids[0], "title": "first"} if step_ids else None,
        "steps": [{"step": i + 1, "id": sid, "title": sid} for i, sid in enumerate(step_ids)],
    }


def test_create_plan_creates_the_plan_then_each_step_in_order():
    posts = []

    def transport(request):
        if request.method == "POST":
            posts.append(json.loads(request.content))
            return httpx.Response(201, json=posts[-1])
        if request.url.path.endswith("/plan"):
            return httpx.Response(200, json=plan_body([]))
        return httpx.Response(200, json={"id": PLAN_ID, "priority": "CRITICAL", "startDate": "2026-10-12"})

    result = json.loads(
        build_handler(transport)(
            action="create_plan",
            id=PLAN_ID,
            title="Bothell move",
            steps=[{"title": "Book the movers", "startDate": "2026-10-13"}, {"title": "Pack the kitchen"}],
        )
    )

    assert result["success"] is True
    assert posts[0]["id"] == PLAN_ID and posts[0]["title"] == "Bothell move"
    assert [(p["title"], p["parentTaskId"], p["position"]) for p in posts[1:]] == [
        ("Book the movers", PLAN_ID, 0),
        ("Pack the kitchen", PLAN_ID, 1),
    ]
    assert posts[1]["startDate"] == "2026-10-13"
    assert posts[2]["startDate"] == "2026-10-12"  # the plan's day
    assert posts[2]["priority"] == "CRITICAL"


def test_add_steps_moves_existing_tasks_in_with_a_state_hash():
    writes = []

    def transport(request):
        if request.method == "PATCH":
            writes.append((request.url.path, request.headers.get("X-MYN-State-Hash"), json.loads(request.content)))
            return httpx.Response(200, json={})
        if request.url.path.endswith("/plan"):
            return httpx.Response(200, json=plan_body([STEP_A]))
        return httpx.Response(200, json={"id": STEP_B, "stateHash": "hash-v1"})

    result = json.loads(
        build_handler(transport)(action="add_steps", taskId=PLAN_ID, stepIds=[STEP_B])
    )

    assert result["success"] is True
    assert writes == [
        (f"/api/v2/unified-tasks/{STEP_B}", "hash-v1", {"parentTaskId": PLAN_ID, "position": 1})
    ]


def test_plan_reads_the_current_step():
    def transport(request):
        assert request.url.path == f"/api/v2/unified-tasks/{STEP_A}/plan"
        return httpx.Response(200, json=plan_body([STEP_A, STEP_B]))

    result = json.loads(build_handler(transport)(action="plan", taskId=STEP_A))

    assert result["data"]["currentStep"]["id"] == STEP_A


def test_reorder_steps_patches_only_moved_steps():
    writes = []

    def transport(request):
        if request.method == "PATCH":
            writes.append((request.url.path, json.loads(request.content)))
            return httpx.Response(200, json={})
        if request.url.path.endswith("/plan"):
            return httpx.Response(200, json=plan_body([STEP_A, STEP_B]))
        return httpx.Response(200, json={"stateHash": "hash-v1"})

    result = json.loads(
        build_handler(transport)(action="reorder_steps", taskId=PLAN_ID, stepIds=[STEP_B])
    )

    assert result["success"] is True
    assert writes == [
        (f"/api/v2/unified-tasks/{STEP_B}", {"position": 0}),
        (f"/api/v2/unified-tasks/{STEP_A}", {"position": 1}),
    ]


def test_reorder_steps_rejects_a_task_outside_the_plan():
    def transport(request):
        return httpx.Response(200, json=plan_body([STEP_A]))

    result = json.loads(
        build_handler(transport)(action="reorder_steps", taskId=PLAN_ID, stepIds=[STEP_B])
    )

    assert result["success"] is False
    assert "Not steps of this plan" in result["error"]
