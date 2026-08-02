import json
import sys
import types
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

import mind_your_now.tools as tool_framework
from mind_your_now.client import MynApiClient
from mind_your_now.tools import planning
from mind_your_now.tools.planning import register_planning_tool


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
    register_planning_tool(context, client, lambda: True)
    return context.registration["handler"]


@pytest.mark.parametrize(
    ("input_data", "method", "path"),
    [
        ({"action": "plan"}, "GET", "/planning/plan"),
        ({"action": "schedule_all"}, "POST", "/planning/scheduleAll"),
        ({"action": "reschedule"}, "POST", "/planning/kickTheCan"),
    ],
)
def test_actions_use_expected_methods_and_paths(input_data, method, path):
    observed = []

    def transport(request):
        observed.append((request.method, request.url.path))
        return httpx.Response(200, json="planned")

    result = json.loads(build_handler(transport)(**input_data))

    assert observed == [(method, path)]
    expected = "planned" if input_data["action"] == "reschedule" else {"result": "planned"}
    assert result == {"success": True, "data": expected}


def test_reschedule_passes_rebalance_via_params():
    observed = []

    def transport(request):
        observed.append(dict(request.url.params))
        return httpx.Response(200, json={"ok": True})

    handler = build_handler(transport)
    handler(action="reschedule", spreadOverDays=1)
    handler(action="reschedule", spreadOverDays=3)

    assert observed == [{"rebalance": "false"}, {"rebalance": "true"}]


def test_schema_advertises_implemented_read_only_dry_run():
    from mind_your_now.tools.planning import PLANNING_SCHEMA

    assert PLANNING_SCHEMA["properties"]["dryRun"]["type"] == "boolean"
    assert (
        "engine decisions"
        in PLANNING_SCHEMA["properties"]["dryRun"]["description"].lower()
    )


@pytest.fixture
def customer_now(monkeypatch):
    zone = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 8, 1, 23, 30, tzinfo=zone)
    monkeypatch.setattr(planning, "_now_in_zone", lambda resolved_zone: now)
    return now


def planning_transport(tasks, observed):
    def transport(request):
        observed.append((request.method, request.url.path, dict(request.url.params)))
        assert request.method == "GET"
        if request.url.path == "/api/v1/customers/planning-context":
            return httpx.Response(
                200,
                json={
                    "id": 42,
                    "defaultTimeZone": "America/Los_Angeles",
                },
            )
        assert request.url.path == "/api/v2/unified-tasks"
        return httpx.Response(
            200,
            json={
                "tasks": [
                    {"ownerId": 42, **task}
                    for task in tasks
                ]
            },
        )

    return transport


@pytest.mark.parametrize(
    ("action", "spread_over_days", "expected_ids"),
    [
        ("schedule_all", 1, ["eligible", "habit"]),
        ("reschedule", 1, ["eligible"]),
        ("reschedule", 3, ["eligible", "future"]),
    ],
)
def test_dry_run_returns_customer_local_candidates_without_mutation(
    customer_now, action, spread_over_days, expected_ids
):
    observed = []
    tasks = [
        {
            "id": "eligible",
            "title": "Eligible",
            "taskType": "TASK",
            "priority": "CRITICAL",
            "startDate": "2026-08-02T06:30:00Z",
            "isCompleted": False,
            "isAutoScheduled": False,
        },
        {
            "id": "future",
            "title": "Future",
            "taskType": "TASK",
            "priority": "CRITICAL",
            "startDate": "2026-08-03T07:00:00Z",
            "isCompleted": False,
            "isAutoScheduled": False,
        },
        {
            "id": "completed",
            "title": "Completed",
            "taskType": "TASK",
            "priority": "CRITICAL",
            "startDate": "2026-08-02T06:30:00Z",
            "isCompleted": True,
            "isAutoScheduled": False,
        },
        {
            "id": "habit",
            "title": "Habit",
            "taskType": "HABIT",
            "priority": "CRITICAL",
            "startDate": "2026-08-02T06:30:00Z",
            "isCompleted": False,
            "isAutoScheduled": False,
        },
    ]

    result = json.loads(
        build_handler(planning_transport(tasks, observed))(
            action=action,
            spreadOverDays=spread_over_days,
            dryRun=True,
        )
    )

    assert observed == [
        ("GET", "/api/v1/customers/planning-context", {}),
        (
            "GET",
            "/api/v2/unified-tasks",
            {"detail": "full", "limit": "200", "offset": "0"},
        ),
    ]
    assert result["success"] is True
    assert [task["id"] for task in result["data"]["tasks"]] == expected_ids
    assert result["data"]["count"] == len(expected_ids)
    assert result["data"]["customerTimeZone"] == "America/Los_Angeles"
    assert result["data"]["dryRun"] is True
    assert result["data"]["engineDecisionsPreviewed"] is False
    assert "50 pages or 10,000 tasks" in result["data"]["message"]


def test_dry_run_marks_candidate_count_incomplete_at_scan_cap(
    customer_now, monkeypatch
):
    monkeypatch.setattr(tool_framework, "UNIFIED_TASK_MAX_PAGES", 2)
    task_requests = 0

    def transport(request):
        nonlocal task_requests
        if request.url.path == "/api/v1/customers/planning-context":
            return httpx.Response(
                200,
                json={"id": 42, "defaultTimeZone": "America/Los_Angeles"},
            )

        task_requests += 1
        offset = int(request.url.params["offset"])
        return httpx.Response(
            200,
            json={
                "tasks": [
                    {
                        "id": f"task-{index}",
                        "ownerId": 42,
                        "taskType": "TASK",
                        "priority": "CRITICAL",
                        "startDate": "2026-08-02T06:30:00Z",
                        "isCompleted": False,
                        "isAutoScheduled": False,
                    }
                    for index in range(offset, offset + 200)
                ],
                "hasMore": True,
                "snapshot": "stable-generation",
            },
        )

    result = json.loads(
        build_handler(transport)(action="schedule_all", dryRun=True)
    )

    assert task_requests == 2
    assert result["data"]["collectionComplete"] is False
    assert result["data"]["countIsLowerBound"] is True
    assert result["data"]["_truncated"] is True
    assert "_totalCount" not in result["data"]


@pytest.mark.parametrize(
    ("action", "spread_over_days"),
    [
        ("schedule_all", 1),
        ("reschedule", 1),
        ("reschedule", 3),
    ],
)
def test_dry_run_excludes_tasks_only_assigned_to_customer(
    customer_now, action, spread_over_days
):
    observed = []
    tasks = [
        {
            "id": "owned",
            "ownerId": 42,
            "taskType": "TASK",
            "priority": "CRITICAL",
            "startDate": "2026-08-02T06:30:00Z",
            "isCompleted": False,
            "isAutoScheduled": False,
        },
        {
            "id": "assigned",
            "ownerId": 99,
            "taskType": "TASK",
            "priority": "CRITICAL",
            "startDate": "2026-08-02T06:30:00Z",
            "isCompleted": False,
            "isAutoScheduled": False,
        },
    ]

    result = json.loads(
        build_handler(planning_transport(tasks, observed))(
            action=action,
            spreadOverDays=spread_over_days,
            dryRun=True,
        )
    )

    assert [task["id"] for task in result["data"]["tasks"]] == ["owned"]
    assert result["data"]["count"] == 1


def test_schedule_all_includes_exact_next_local_midnight(customer_now):
    observed = []
    tasks = [
        {
            "id": "at-cutoff",
            "priority": "CRITICAL",
            "startDate": "2026-08-02T07:00:00Z",
        },
        {
            "id": "after-cutoff",
            "priority": "CRITICAL",
            "startDate": "2026-08-02T07:00:00.001Z",
        },
    ]

    result = json.loads(
        build_handler(planning_transport(tasks, observed))(
            action="schedule_all",
            dryRun=True,
        )
    )

    assert [task["id"] for task in result["data"]["tasks"]] == ["at-cutoff"]


def test_reschedule_uses_customer_local_date_boundary(customer_now):
    observed = []
    tasks = [
        {
            "id": "customer-today",
            "taskType": "TASK",
            "startDate": "2026-08-02T06:59:59Z",
        },
        {
            "id": "customer-tomorrow",
            "taskType": "TASK",
            "startDate": "2026-08-02T07:00:00Z",
        },
    ]

    result = json.loads(
        build_handler(planning_transport(tasks, observed))(
            action="reschedule",
            spreadOverDays=1,
            dryRun=True,
        )
    )

    assert [task["id"] for task in result["data"]["tasks"]] == [
        "customer-today"
    ]


@pytest.mark.parametrize(
    ("preview_limit", "expected_length"),
    [(None, 50), (2, 2)],
)
def test_dry_run_preview_limit_preserves_full_count(
    customer_now, preview_limit, expected_length
):
    observed = []
    tasks = [
        {
            "id": f"task-{index:03d}",
            "priority": "CRITICAL",
            "startDate": "2026-08-01T12:00:00Z",
        }
        for index in range(60)
    ]
    arguments = {"action": "schedule_all", "dryRun": True}
    if preview_limit is not None:
        arguments["previewLimit"] = preview_limit

    result = json.loads(
        build_handler(planning_transport(tasks, observed))(**arguments)
    )

    assert len(result["data"]["tasks"]) == expected_length
    assert result["data"]["count"] == 60
    assert result["data"]["_truncated"] is True
    assert result["data"]["_totalCount"] == 60


@pytest.mark.parametrize("preview_limit", [0, 201, 1.5, True])
def test_dry_run_rejects_invalid_preview_limit_before_api_request(preview_limit):
    requests = []

    result = json.loads(
        build_handler(lambda request: requests.append(request))(
            action="schedule_all",
            dryRun=True,
            previewLimit=preview_limit,
        )
    )

    assert result["success"] is False
    assert "previewLimit" in result["error"]
    assert requests == []


def test_plan_rejects_dry_run_before_api_request():
    requests = []

    result = json.loads(
        build_handler(lambda request: requests.append(request))(
            action="plan",
            dryRun=True,
        )
    )

    assert result == {
        "success": False,
        "error": "dryRun is supported only for schedule_all and reschedule",
    }
    assert requests == []
