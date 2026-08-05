"""Acceptance criteria verification tests for MIN-931."""

import json
import sys
import types

import httpx
import pytest

from mind_your_now.client import MynApiClient
from mind_your_now.tools.memory import register_memory_tool
from mind_your_now.tools.ynab import register_ynab_tool
from mind_your_now.tools.planning import register_planning_tool
from mind_your_now.tools.lists import register_lists_tool
from mind_your_now.tools.habits import register_habits_tool


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


# WI-5: recall_relevant AC
def test_wi5_ac1_recall_relevant_sends_query_and_describes_semantic():
    """AC: recall_relevant sends query to /api/v1/agent/memories/context and describes semantic matches."""
    observed = []

    def transport(request):
        observed.append((request.method, request.url.path, dict(request.url.params)))
        return httpx.Response(200, json={"memories": []})

    client = MynApiClient(
        "https://api.example.com",
        "myn-key",
        transport=httpx.MockTransport(transport),
    )
    context = Context()
    register_memory_tool(context, client, lambda: True)

    # Call recall_relevant action
    result = json.loads(context.registration["handler"](action="recall_relevant", query="test"))

    # Verify endpoint and query param
    assert observed[-1] == ("GET", "/api/v1/agent/memories/context", {"query": "test", "limit": "10"})
    assert result["success"] is True

    # Verify description mentions semantic matches
    schema = context.registration["schema"]
    assert "semantic" in schema["parameters"]["properties"]["query"]["description"].lower()


def test_wi5_ac2_search_remains_deprecated_alias():
    """AC: search remains callable as deprecated alias with deprecation note in schema description."""
    client = MynApiClient("https://api.example.com", "myn-key")
    context = Context()
    register_memory_tool(context, client, lambda: True)

    schema = context.registration["schema"]
    # Verify search is in actions
    assert "search" in schema["parameters"]["properties"]["action"]["enum"]
    assert "recall_relevant" in schema["parameters"]["properties"]["action"]["enum"]

    # Verify description contains deprecation note
    description = context.registration["description"]
    assert "deprecated" in description.lower()
    assert "recall_relevant" in description.lower()


def test_wi5_ac3_module_docstring_records_min932_gap():
    """AC: Module docstring records filtered-search gap as MIN-932 dependency."""
    from mind_your_now.tools import memory

    assert "MIN-932" in memory.__doc__
    assert "filtered" in memory.__doc__.lower() or "search" in memory.__doc__.lower()


# WI-6a: search_payees bounded AC
def test_wi6a_ac1_search_payees_requires_query():
    """AC: search_payees without query returns error and never issues unbounded GET."""
    observed = []

    def transport(request):
        observed.append((request.method, request.url.path))
        return httpx.Response(200, json={"payees": []})

    client = MynApiClient(
        "https://api.example.com",
        "myn-key",
        transport=httpx.MockTransport(transport),
    )
    context = Context()
    register_ynab_tool(context, client, lambda: True)

    # Call without query should error
    result = json.loads(context.registration["handler"](action="search_payees"))

    assert result["success"] is False
    assert "query" in result["error"].lower() or "payeename" in result["error"].lower()
    # Verify unbounded /payees endpoint was NOT called
    assert not any("/payees" in path and "search" not in path for _, path in observed)


def test_wi6a_ac2_limit_enforced_with_truncate_markers():
    """AC: limit is forwarded and enforced client-side via truncate with _truncated/_totalCount."""
    observed = []

    def transport(request):
        observed.append((request.method, request.url.path))
        return httpx.Response(200, json={"payees": [
            {"id": "1", "name": "A"},
            {"id": "2", "name": "B"},
            {"id": "3", "name": "C"},
            {"id": "4", "name": "D"},
        ]})

    client = MynApiClient(
        "https://api.example.com",
        "myn-key",
        transport=httpx.MockTransport(transport),
    )
    context = Context()
    register_ynab_tool(context, client, lambda: True)

    # Call with limit
    result = json.loads(context.registration["handler"](action="search_payees", payeeName="test", limit=2))

    data = result["data"]
    assert len(data["payees"]) == 2
    assert data.get("_truncated") is True
    assert data.get("_totalCount") == 4


def test_wi6a_ac3_schema_has_query_and_limit():
    """AC: Schema contains query (alias) and limit fields with descriptions."""
    client = MynApiClient("https://api.example.com", "myn-key")
    context = Context()
    register_ynab_tool(context, client, lambda: True)

    schema = context.registration["schema"]
    props = schema["parameters"]["properties"]
    assert "query" in props
    assert props["query"].get("description")
    assert "limit" in props
    assert props["limit"].get("description")


# WI-6b: list_transactions windowed AC
def test_wi6b_ac1_list_transactions_honors_date_range_and_limit():
    """AC: list_transactions honors sinceDate, untilDate, and limit with client-side filtering."""
    observed = []

    def transport(request):
        observed.append((request.method, request.url.path, dict(request.url.params)))
        return httpx.Response(200, json={"transactions": [
            {"id": "1", "date": "2026-08-01", "amount": 100},
            {"id": "2", "date": "2026-08-15", "amount": 200},
            {"id": "3", "date": "2026-09-01", "amount": 300},
        ]})

    client = MynApiClient(
        "https://api.example.com",
        "myn-key",
        transport=httpx.MockTransport(transport),
    )
    context = Context()
    register_ynab_tool(context, client, lambda: True)

    # Call with untilDate
    result = json.loads(context.registration["handler"](
        action="list_transactions",
        sinceDate="2026-08-01",
        untilDate="2026-08-31",
        limit=2
    ))

    data = result["data"]
    # Should filter to dates <= untilDate and limit to 2
    assert len(data["transactions"]) == 2
    assert all(t["date"] <= "2026-08-31" for t in data["transactions"])


def test_wi6b_ac3_schema_describes_narrow_date_range_preference():
    """AC: Schema description for untilDate/limit contains guidance about narrow date ranges."""
    client = MynApiClient("https://api.example.com", "myn-key")
    context = Context()
    register_ynab_tool(context, client, lambda: True)

    schema = context.registration["schema"]
    props = schema["parameters"]["properties"]
    assert "untilDate" in props
    desc = props["untilDate"].get("description", "").lower()
    assert "narrow" in desc or "preferred" in desc or "recommend" in desc


# WI-6c: list_budgets AC
def test_wi6c_ac1_list_budgets_dispatches_to_endpoint():
    """AC: list_budgets dispatches to the budgets endpoint."""
    observed = []

    def transport(request):
        observed.append((request.method, request.url.path))
        return httpx.Response(200, json={"budgets": [{"id": "1", "name": "Monthly"}]})

    client = MynApiClient(
        "https://api.example.com",
        "myn-key",
        transport=httpx.MockTransport(transport),
    )
    context = Context()
    register_ynab_tool(context, client, lambda: True)

    result = json.loads(context.registration["handler"](action="list_budgets"))

    assert observed[-1] == ("GET", "/api/v1/ynab/budget/budgets")
    assert result["success"] is True


# WI-7a: planning schema honesty AC
def test_wi7a_ac1_planning_schema_contains_only_supported_parameters():
    """AC: PLANNING_SCHEMA contains only action, spreadOverDays, and dryRun."""
    from mind_your_now.tools.planning import PLANNING_SCHEMA

    # Schema structure: properties are at top level for action_schema output
    props = PLANNING_SCHEMA.get("properties", PLANNING_SCHEMA.get("parameters", {}).get("properties", {}))
    # Should only have spreadOverDays (action is implicit in action_schema)
    assert "spreadOverDays" in props
    # Verify ignored params are gone
    assert "goal" not in props
    assert "constraints" not in props
    assert "tasks" not in props
    assert "date" not in props
    assert props["dryRun"]["type"] == "boolean"


def test_wi7a_ac2_description_warns_about_user_wide_scope():
    """AC: Description warns actions are user-wide and mutate state."""
    client = MynApiClient("https://api.example.com", "myn-key")
    context = Context()
    register_planning_tool(context, client, lambda: True)

    description = context.registration["description"]
    assert "user-wide" in description.lower()
    assert "mutate" in description.lower() or "permission" in description.lower()


def test_wi7a_ac3_module_docstring_records_min932_dependency():
    """AC: Module docstring records scoped planning as MIN-932 dependency."""
    from mind_your_now.tools import planning

    assert "MIN-932" in planning.__doc__


# WI-7b: lists toggle AC
def test_wi7b_ac1_toggle_only_patches_when_state_differs():
    """AC: toggle issues PATCH only when current state differs from requested."""
    observed = []

    def transport(request):
        observed.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"id": "item-1", "checked": False})
        return httpx.Response(200, json={"id": "item-1", "checked": True})

    client = MynApiClient(
        "https://api.example.com",
        "myn-key",
        transport=httpx.MockTransport(transport),
    )
    context = Context()
    register_lists_tool(context, client, lambda: True)

    # Call toggle with checked=True when currently False - should PATCH
    result = json.loads(context.registration["handler"](
        action="toggle",
        householdId="hh-1",
        itemId="item-1",
        checked=True
    ))

    # Should have GET then PATCH
    assert observed[0][0] == "GET"
    assert observed[1][0] == "PATCH"


def test_wi7b_ac2_already_current_state_no_write():
    """AC: Requesting already-current state sends no write."""
    observed = []

    def transport(request):
        observed.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"id": "item-1", "checked": True})
        return httpx.Response(200, json={"id": "item-1"})

    client = MynApiClient(
        "https://api.example.com",
        "myn-key",
        transport=httpx.MockTransport(transport),
    )
    context = Context()
    register_lists_tool(context, client, lambda: True)

    # Call toggle with checked=True when already True - should NOT PATCH
    result = json.loads(context.registration["handler"](
        action="toggle",
        householdId="hh-1",
        itemId="item-1",
        checked=True
    ))

    # Should have only GET, no PATCH
    assert len(observed) == 1
    assert observed[0][0] == "GET"


# WI-7c: habits schedule lists habits AC
def test_wi7c_ac1_schedule_returns_habits_via_unified_tasks():
    """AC: schedule returns habits via GET /api/v2/unified-tasks?taskType=HABIT trimmed through truncate."""
    observed = []

    def transport(request):
        observed.append((request.method, request.url.path, dict(request.url.params)))
        return httpx.Response(200, json={"tasks": [
            {"id": "h1", "title": "Morning", "taskType": "HABIT"},
            {"id": "h2", "title": "Evening", "taskType": "HABIT"},
        ]})

    client = MynApiClient(
        "https://api.example.com",
        "myn-key",
        transport=httpx.MockTransport(transport),
    )
    context = Context()
    register_habits_tool(context, client, lambda: True)

    result = json.loads(context.registration["handler"](action="schedule"))

    assert observed[-1][0] == "GET"
    assert observed[-1][1] == "/api/v2/unified-tasks"
    assert "type" in observed[-1][2]
    assert observed[-1][2]["type"] == "HABIT"
    assert result["success"] is True


# MIN-934: habits reminders restored through unified tasks
def test_min934_reminders_action_and_fields_are_in_schema():
    """AC: habits schema advertises reminder reads and writes on task fields."""
    from mind_your_now.tools.habits import HABITS_SCHEMA

    props = HABITS_SCHEMA.get("properties", HABITS_SCHEMA.get("parameters", {}).get("properties", {}))
    actions = props["action"]["enum"]
    assert "reminders" in actions
    assert "unified task entity" in props["enableReminders"]["description"]
    assert "unified task entity" in props["reminderTime"]["description"]


def test_min934_module_docstring_no_longer_marks_reminders_blocked():
    """AC: Module documentation describes reminders as a supported habit action."""
    from mind_your_now.tools import habits

    assert "reminders" in habits.__doc__
    assert "blocked" not in habits.__doc__


# WI-9b: Redact secrets AC
def test_wi9b_ac1_tool_result_redacts_secrets():
    """AC: tool_result redacts secret-shaped keys recursively."""
    from mind_your_now.tools import tool_result
    import json

    # Test payload with secrets at various levels
    payload = {
        "accounts": [
            {"email": "user@example.com", "refreshToken": "ya29.secret"},
        ],
        "apiKey": "sk-123456789",
        "token": "bearer-value",
        "authorization": "Bearer secret",
        "cookie": "session=secret",
        "settings": {
            "password": "my_password",
            "sessionToken": "session-secret",
            "username": "john",
        },
    }

    # Convert through tool_result (which redacts before passing to hermes)
    result_str = tool_result(payload)
    result_obj = json.loads(result_str)

    # Extract the data from the hermes wrapper
    if "data" in result_obj:
        data = result_obj["data"]
    else:
        data = result_obj

    # Verify redaction
    assert data["apiKey"] == "[REDACTED]"
    assert data["token"] == "[REDACTED]"
    assert data["authorization"] == "[REDACTED]"
    assert data["cookie"] == "[REDACTED]"
    assert data["settings"]["password"] == "[REDACTED]"
    assert data["settings"]["sessionToken"] == "[REDACTED]"
    # Email should NOT be redacted (not a secret key)
    assert data["accounts"][0]["email"] == "user@example.com"
    # refreshToken should be redacted
    assert data["accounts"][0]["refreshToken"] == "[REDACTED]"


# WI-8a: dryRun lists AC
def test_wi8a_ac1_delete_checked_dryrun_returns_items():
    """AC: delete_checked with dryRun: true returns checked items with count and dryRun flag, no DELETE."""
    observed = []

    def transport(request):
        observed.append((request.method, request.url.path))
        return httpx.Response(200, json={"items": [
            {"id": "1", "checked": True, "name": "Item 1"},
            {"id": "2", "checked": False, "name": "Item 2"},
        ]})

    client = MynApiClient(
        "https://api.example.com",
        "myn-key",
        transport=httpx.MockTransport(transport),
    )
    context = Context()
    register_lists_tool(context, client, lambda: True)

    result = json.loads(context.registration["handler"](
        action="delete_checked",
        householdId="hh-1",
        dryRun=True
    ))

    data = result["data"]
    assert data.get("dryRun") is True
    assert len(data.get("items", [])) >= 1  # Should have checked items
    assert data.get("count") is not None
    # Should NOT have DELETE in observed
    assert not any(m == "DELETE" for m, _ in observed)
