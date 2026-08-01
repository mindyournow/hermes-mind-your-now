import json
import sys
import types

import httpx
import pytest

from mind_your_now.client import MynApiClient
from mind_your_now.tools.ynab import ACTIONS, register_ynab_tool


TRANSACTION_ID = "txn-1"
CATEGORY_ID = "cat-1"
GROUP_ID = "group-1"


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


def ynab_transport(observed):
    def transport(request):
        observed.append((request.method, request.url.path, dict(request.url.params)))
        path = request.url.path
        if path == "/api/v1/ynab/budget/categories/search":
            return httpx.Response(200, json={"id": CATEGORY_ID, "name": "Groceries"})
        if path == "/api/v1/ynab/budget/categories" and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "categoryGroups": [
                        {"id": GROUP_ID, "name": "Needs", "categories": []}
                    ]
                },
            )
        if path == "/api/v1/ynab/budget/accounts":
            return httpx.Response(
                200,
                json={
                    "checking": [
                        {
                            "id": "acct-2",
                            "name": "Savings",
                            "transferPayeeId": "transfer-1",
                        }
                    ],
                    "savings": [],
                    "creditCards": [],
                    "loans": [],
                },
            )
        if path == "/api/v1/ynab/transactions" and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "transactions": [
                        {
                            "id": TRANSACTION_ID,
                            "amount": -1000,
                            "date": "2026-07-25",
                            "payee_name": "Store",
                            "deleted": False,
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"amount": 1000, "ok": True})

    return transport


def build_handler(observed):
    client = MynApiClient(
        "https://api.example.com",
        "myn-key",
        transport=httpx.MockTransport(ynab_transport(observed)),
    )
    context = Context()
    register_ynab_tool(context, client, lambda: True)
    return context.registration["handler"]


CASES = [
    ({"action": "budget_overview"}, "GET", "/api/v1/ynab/budget/overview"),
    (
        {"action": "category_balance", "categoryName": "Groceries"},
        "GET",
        "/api/v1/ynab/budget/categories/search",
    ),
    ({"action": "list_categories"}, "GET", "/api/v1/ynab/budget/categories"),
    ({"action": "list_budgets"}, "GET", "/api/v1/ynab/budget/budgets"),
    ({"action": "account_balances"}, "GET", "/api/v1/ynab/budget/accounts"),
    (
        {"action": "set_budget_amount", "categoryName": "Groceries", "amount": 200},
        "PATCH",
        f"/api/v1/ynab/budget/categories/{CATEGORY_ID}/budget",
    ),
    (
        {
            "action": "set_category_goal",
            "categoryName": "Groceries",
            "goalType": "TBD",
        },
        "PATCH",
        f"/api/v1/ynab/budget/categories/{CATEGORY_ID}/goal",
    ),
    ({"action": "goal_progress"}, "GET", "/api/v1/ynab/budget/categories"),
    ({"action": "budget_months"}, "GET", "/api/v1/ynab/budget/months"),
    (
        {"action": "search_payees", "payeeName": "Bread & Butter"},
        "GET",
        "/api/v1/ynab/budget/payees/search",
    ),
    (
        {
            "action": "create_transaction",
            "accountId": "acct-1",
            "payeeName": "Store",
            "amount": -2,
            "categoryName": "Groceries",
            "date": "2026-07-25",
        },
        "POST",
        "/api/v1/ynab/transactions",
    ),
    (
        {
            "action": "create_transactions_bulk",
            "transactions": [
                {
                    "accountId": "acct-1",
                    "payeeName": "Store",
                    "amount": -2,
                    "categoryName": "Groceries",
                }
            ],
        },
        "POST",
        "/api/v1/ynab/transactions/bulk",
    ),
    ({"action": "list_transactions"}, "GET", "/api/v1/ynab/transactions"),
    (
        {"action": "update_transaction", "transactionId": TRANSACTION_ID, "memo": "Updated"},
        "PUT",
        f"/api/v1/ynab/transactions/{TRANSACTION_ID}",
    ),
    (
        {"action": "delete_transaction", "transactionId": TRANSACTION_ID},
        "DELETE",
        f"/api/v1/ynab/transactions/{TRANSACTION_ID}",
    ),
    (
        {
            "action": "split_transaction",
            "transactionId": TRANSACTION_ID,
            "splits": [
                {"categoryName": "Food", "amount": -0.5},
                {"categoryName": "Home", "amount": -0.5},
            ],
        },
        "PUT",
        f"/api/v1/ynab/transactions/{TRANSACTION_ID}",
    ),
    (
        {"action": "scheduled_transactions"},
        "GET",
        "/api/v1/ynab/scheduled-transactions",
    ),
    (
        {
            "action": "create_scheduled_transaction",
            "accountId": "acct-1",
            "payeeName": "Rent",
            "amount": -1000,
            "frequency": "monthly",
            "dateFirst": "2026-08-01",
        },
        "POST",
        "/api/v1/ynab/scheduled-transactions",
    ),
    (
        {
            "action": "update_scheduled_transaction",
            "transactionId": TRANSACTION_ID,
            "memo": "Updated",
        },
        "PUT",
        f"/api/v1/ynab/scheduled-transactions/{TRANSACTION_ID}",
    ),
    (
        {"action": "delete_scheduled_transaction", "transactionId": TRANSACTION_ID},
        "DELETE",
        f"/api/v1/ynab/scheduled-transactions/{TRANSACTION_ID}",
    ),
    ({"action": "subscriptions"}, "GET", "/api/v1/ynab/subscriptions"),
    ({"action": "upcoming_bills"}, "GET", "/api/v1/ynab/scheduled"),
    (
        {"action": "spending_insights"},
        "GET",
        "/api/v1/ynab/analytics/spending",
    ),
    ({"action": "payee_analysis"}, "GET", "/api/v1/ynab/analytics/payees"),
    ({"action": "spending_trends"}, "GET", "/api/v1/ynab/analytics/trends"),
    ({"action": "net_worth"}, "GET", "/api/v1/ynab/analytics/net-worth"),
    ({"action": "debt_tracking"}, "GET", "/api/v1/ynab/analytics/debt"),
    (
        {"action": "create_category_group", "groupName": "New Group"},
        "POST",
        "/api/v1/ynab/budget/category-groups",
    ),
    (
        {
            "action": "create_category",
            "categoryName": "New Category",
            "categoryGroupId": GROUP_ID,
        },
        "POST",
        "/api/v1/ynab/budget/categories",
    ),
    (
        {"action": "rename_category", "categoryName": "Old", "newName": "New"},
        "PATCH",
        f"/api/v1/ynab/budget/categories/{CATEGORY_ID}/details",
    ),
    (
        {
            "action": "move_category",
            "categoryName": "Old",
            "categoryGroupId": GROUP_ID,
        },
        "PATCH",
        f"/api/v1/ynab/budget/categories/{CATEGORY_ID}/details",
    ),
    (
        {"action": "rename_category_group", "groupName": "Needs", "newName": "Core"},
        "PATCH",
        f"/api/v1/ynab/budget/category-groups/{GROUP_ID}",
    ),
    ({"action": "connection_status"}, "GET", "/api/v1/ynab/status"),
]


@pytest.mark.parametrize(("input_data", "method", "path"), CASES)
def test_each_action_uses_expected_final_method_and_path(input_data, method, path):
    observed = []
    result = json.loads(build_handler(observed)(**input_data))

    assert observed[-1][:2] == (method, path)
    assert result["success"] is True


def test_action_cases_cover_every_schema_action():
    assert {case[0]["action"] for case in CASES} == set(ACTIONS)


def test_milliunit_fields_are_recursively_formatted():
    observed = []

    def transport(request):
        observed.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "readyToAssign": 1234567,
                "accounts": [{"balance": -5050, "name": "Checking"}],
                "count": 2,
            },
        )

    client = MynApiClient(
        "https://api.example.com",
        "myn-key",
        transport=httpx.MockTransport(transport),
    )
    context = Context()
    register_ynab_tool(context, client, lambda: True)

    result = json.loads(context.registration["handler"](action="budget_overview"))

    assert result["data"] == {
        "readyToAssign": "$1,234.57",
        "accounts": [{"balance": "-$5.05", "name": "Checking"}],
        "count": 2,
    }


def test_query_values_are_encoded_through_params():
    observed = []
    handler = build_handler(observed)

    handler(action="search_payees", payeeName="Bread & Butter")
    handler(
        action="spending_insights",
        months=4,
        categoryName="Dining & Drinks",
    )

    assert observed == [
        (
            "GET",
            "/api/v1/ynab/budget/payees/search",
            {"query": "Bread & Butter"},
        ),
        (
            "GET",
            "/api/v1/ynab/analytics/spending",
            {"months": "4", "category": "Dining & Drinks"},
        ),
    ]
