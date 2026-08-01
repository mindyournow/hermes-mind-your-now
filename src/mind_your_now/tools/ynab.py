"""myn_ynab: YNAB budget, transaction, schedule, and analytics access."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from mind_your_now.client import MynApiClient
from mind_your_now.schemas import action_schema
from mind_your_now.tools import register_myn_tool, tool_error, tool_result


logger = logging.getLogger(__name__)
ACTIONS = [
    "budget_overview",
    "category_balance",
    "list_categories",
    "list_budgets",
    "account_balances",
    "set_budget_amount",
    "set_category_goal",
    "goal_progress",
    "budget_months",
    "search_payees",
    "create_transaction",
    "create_transactions_bulk",
    "list_transactions",
    "update_transaction",
    "delete_transaction",
    "scheduled_transactions",
    "create_scheduled_transaction",
    "update_scheduled_transaction",
    "delete_scheduled_transaction",
    "subscriptions",
    "upcoming_bills",
    "spending_insights",
    "payee_analysis",
    "spending_trends",
    "net_worth",
    "debt_tracking",
    "split_transaction",
    "create_category_group",
    "create_category",
    "rename_category",
    "move_category",
    "rename_category_group",
    "connection_status",
]
MILLIUNIT_FIELDS = {
    "readyToAssign",
    "totalIncome",
    "totalBudgeted",
    "totalActivity",
    "balance",
    "budgeted",
    "activity",
    "clearedBalance",
    "unclearedBalance",
    "goalTarget",
    "goalUnderFunded",
    "goalOverFunded",
    "goalOverallFunded",
    "goalOverallLeft",
    "amount",
    "total",
    "totalSpending",
    "monthlyTotal",
    "annualTotal",
    "amountMilliunits",
    "totalSpent",
    "monthlyAverage",
}

YNAB_SCHEMA = action_schema(
    ACTIONS,
    {
        "categoryName": {"type": "string"},
        "accountId": {"type": "string"},
        "payeeName": {"type": "string"},
        "payeeId": {"type": "string"},
        "query": {"type": "string", "description": "Payee name to search for (alias for payeeName)"},
        "transferToAccount": {"type": "string"},
        "amount": {"type": "number"},
        "date": {"type": "string"},
        "sinceDate": {"type": "string", "description": "Start date for transaction query (YYYY-MM-DD)"},
        "untilDate": {"type": "string", "description": "End date for transaction query, strongly preferred to narrow results (YYYY-MM-DD)"},
        "memo": {"type": "string"},
        "months": {"type": "number"},
        "days": {"type": "number"},
        "month": {"type": "string"},
        "goalType": {"type": "string"},
        "goalTargetDollars": {"type": "number"},
        "goalTargetMonth": {"type": "string"},
        "transactionId": {"type": "string"},
        "cleared": {"type": "string"},
        "flagColor": {"type": "string"},
        "frequency": {"type": "string"},
        "dateFirst": {"type": "string"},
        "groupName": {"type": "string"},
        "newName": {"type": "string"},
        "targetGroupName": {"type": "string"},
        "note": {"type": "string"},
        "categoryGroupId": {"type": "string"},
        "limit": {"type": "number", "description": "Maximum payees to return (client-side truncation)"},
        "splits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "categoryName": {"type": "string"},
                    "amount": {"type": "number"},
                    "memo": {"type": "string"},
                },
                "required": ["categoryName", "amount"],
            },
        },
        "transactions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "accountId": {"type": "string"},
                    "payeeName": {"type": "string"},
                    "amount": {"type": "number"},
                    "categoryName": {"type": "string"},
                    "date": {"type": "string"},
                    "memo": {"type": "string"},
                },
                "required": ["accountId", "payeeName", "amount", "categoryName"],
            },
        },
    },
)


def _format_dollars(milliunits: int | float) -> str:
    dollars = milliunits / 1000
    formatted = f"{abs(dollars):,.2f}"
    return f"-${formatted}" if dollars < 0 else f"${formatted}"


def _convert_milliunits(value: Any) -> Any:
    if isinstance(value, list):
        return [_convert_milliunits(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _format_dollars(item)
            if key in MILLIUNIT_FIELDS and isinstance(item, (int, float))
            else _convert_milliunits(item)
            for key, item in value.items()
        }
    return value


def _round_milliunits(amount: float) -> int:
    return math.floor(amount * 1000 + 0.5)


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _resolve_category_id(client: MynApiClient, name: str) -> str | None:
    result = client.get(
        "/api/v1/ynab/budget/categories/search",
        params={"query": name},
    )
    return result.get("id") if isinstance(result, dict) else None


def _resolve_category_group(
    client: MynApiClient,
    name: str,
) -> dict[str, str] | None:
    data = client.get("/api/v1/ynab/budget/categories")
    groups = data.get("categoryGroups", []) if isinstance(data, dict) else []
    target = name.lower()
    for group in groups:
        if str(group.get("name", "")).lower() == target:
            return {"id": group["id"], "name": group["name"]}
    for group in groups:
        group_name = str(group.get("name", "")).lower()
        if target in group_name or group_name in target:
            return {"id": group["id"], "name": group["name"]}
    return None


def _find_transfer_payee(client: MynApiClient, account_name: str) -> tuple[str | None, str | None]:
    data = client.get("/api/v1/ynab/budget/accounts")
    accounts = []
    if isinstance(data, dict):
        for key in ("checking", "savings", "creditCards", "loans"):
            accounts.extend(data.get(key, []))
    target = account_name.lower()
    match = next(
        (account for account in accounts if target in str(account.get("name", "")).lower()),
        None,
    ) or next(
        (account for account in accounts if str(account.get("name", "")).lower() in target),
        None,
    )
    if not match:
        return None, None
    return match.get("transferPayeeId"), match.get("name")


def execute_ynab(client: MynApiClient, **input_data: Any) -> str:
    action = input_data.get("action")
    direct_gets = {
        "budget_overview": ("/api/v1/ynab/budget/overview", True),
        "list_categories": ("/api/v1/ynab/budget/categories", True),
        "list_budgets": ("/api/v1/ynab/budget/budgets", True),
        "account_balances": ("/api/v1/ynab/budget/accounts", True),
        "goal_progress": ("/api/v1/ynab/budget/categories", True),
        "budget_months": ("/api/v1/ynab/budget/months", True),
        "scheduled_transactions": ("/api/v1/ynab/scheduled-transactions", True),
        "subscriptions": ("/api/v1/ynab/subscriptions", True),
        "net_worth": ("/api/v1/ynab/analytics/net-worth", True),
        "debt_tracking": ("/api/v1/ynab/analytics/debt", True),
        "connection_status": ("/api/v1/ynab/status", False),
    }
    if action in direct_gets:
        path, convert = direct_gets[action]
        data = client.get(path)
        return tool_result(_convert_milliunits(data) if convert else data)

    if action == "category_balance":
        name = input_data.get("categoryName")
        if not name:
            return tool_error("categoryName is required for category_balance action")
        data = client.get(
            "/api/v1/ynab/budget/categories/search", params={"query": name}
        )
        return tool_result(_convert_milliunits(data))

    if action == "set_budget_amount":
        name = input_data.get("categoryName")
        if not name:
            return tool_error("categoryName is required for set_budget_amount action")
        if input_data.get("amount") is None:
            return tool_error(
                "amount is required for set_budget_amount (in dollars, e.g., 200 to budget $200)"
            )
        category_id = _resolve_category_id(client, name)
        if not category_id:
            return tool_error(f"Category '{name}' not found")
        body = {"budgetedDollars": input_data["amount"]}
        if input_data.get("month"):
            body["month"] = input_data["month"]
        return tool_result(
            _convert_milliunits(
                client.patch(
                    f"/api/v1/ynab/budget/categories/{category_id}/budget", body
                )
            )
        )

    if action == "set_category_goal":
        name = input_data.get("categoryName")
        if not name:
            return tool_error("categoryName is required for set_category_goal action")
        if not input_data.get("goalType"):
            return tool_error(
                "goalType is required for set_category_goal action (TB, TBD, MF, NEED)"
            )
        category_id = _resolve_category_id(client, name)
        if not category_id:
            return tool_error(f"Category '{name}' not found")
        month = input_data.get("goalTargetMonth")
        if month and not month.endswith("-01"):
            month += "-01"
        body = {"goalType": input_data["goalType"]}
        if input_data.get("goalTargetDollars") is not None:
            body["goalTargetDollars"] = input_data["goalTargetDollars"]
        if month:
            body["goalTargetMonth"] = month
        return tool_result(
            _convert_milliunits(
                client.patch(
                    f"/api/v1/ynab/budget/categories/{category_id}/goal", body
                )
            )
        )

    if action == "search_payees":
        query = input_data.get("payeeName") or input_data.get("query")
        if not query:
            return tool_error("payeeName (or query) is required for search_payees action")
        from mind_your_now.tools import truncate
        data = client.get(
            "/api/v1/ynab/budget/payees/search",
            params={"query": query},
        )
        if input_data.get("limit"):
            data = truncate(data, "payees", int(input_data["limit"]))
        return tool_result(data)

    if action == "create_transaction":
        if not input_data.get("accountId"):
            return tool_error(
                "accountId is required for create_transaction. Use account_balances to find IDs."
            )
        if input_data.get("amount") is None:
            return tool_error(
                "amount is required for create_transaction (in dollars, negative for expenses)."
            )
        payee_id = input_data.get("payeeId")
        if not payee_id and input_data.get("transferToAccount"):
            payee_id, target_name = _find_transfer_payee(
                client, input_data["transferToAccount"]
            )
            if not target_name:
                return tool_error(
                    f"Transfer target account '{input_data['transferToAccount']}' not found. Use account_balances to see available accounts."
                )
            if not payee_id:
                return tool_error(
                    f"Account '{target_name}' does not have a transfer payee ID."
                )
        if not payee_id and not input_data.get("payeeName"):
            return tool_error(
                "payeeName or transferToAccount is required for create_transaction."
            )
        is_transfer = bool(payee_id or input_data.get("transferToAccount"))
        category_id = None
        if not input_data.get("categoryName") and not is_transfer:
            return tool_error(
                "categoryName is REQUIRED for create_transaction. Use list_categories to find the right category first. Only transfers (using transferToAccount) are exempt from this requirement."
            )
        if input_data.get("categoryName"):
            category_id = _resolve_category_id(client, input_data["categoryName"])
            if not category_id:
                return tool_error(
                    f"Category '{input_data['categoryName']}' not found. Use list_categories to browse."
                )
        amount = _round_milliunits(input_data["amount"])
        transaction_date = input_data.get("date") or _today()
        try:
            existing = client.get(
                "/api/v1/ynab/transactions",
                params={"sinceDate": transaction_date},
            )
            duplicates = [
                item
                for item in (existing.get("transactions", []) if isinstance(existing, dict) else [])
                if not item.get("deleted")
                and item.get("amount") == amount
                and item.get("date") == transaction_date
            ]
            if duplicates:
                info = ", ".join(
                    f"{item.get('payee_name')} {item.get('amount', 0) / 1000}"
                    for item in duplicates
                )
                return tool_error(
                    f"DUPLICATE WARNING: {len(duplicates)} existing transaction(s) found with the same amount (${input_data['amount']}) on {transaction_date}: [{info}]. The bank may have already imported this transaction. Verify with list_transactions before creating. If you are certain this is not a duplicate, use create_transactions_bulk with a single entry to bypass this check."
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[myn_ynab] Duplicate check failed: %s", exc)
        body = {
            "accountId": input_data["accountId"],
            "amountMilliunits": amount,
            "date": transaction_date,
        }
        if payee_id:
            body["payeeId"] = payee_id
        elif input_data.get("payeeName"):
            body["payeeName"] = input_data["payeeName"]
        for field in ("memo", "cleared"):
            if input_data.get(field):
                body[field] = input_data[field]
        if category_id:
            body["categoryId"] = category_id
        return tool_result(
            _convert_milliunits(client.post("/api/v1/ynab/transactions", body))
        )

    if action == "create_transactions_bulk":
        transactions = input_data.get("transactions")
        if not transactions:
            return tool_error(
                "transactions array is required for create_transactions_bulk. Each needs accountId, payeeName, amount."
            )
        resolved = []
        for transaction in transactions:
            if not transaction.get("categoryName"):
                return tool_error(
                    f'categoryName is REQUIRED for transaction "{transaction.get("payeeName")}". Use list_categories to find the right category first.'
                )
            category_id = _resolve_category_id(client, transaction["categoryName"])
            if not category_id:
                return tool_error(
                    f'Category \'{transaction["categoryName"]}\' not found for transaction "{transaction.get("payeeName")}". Use list_categories to browse.'
                )
            entry = {
                "accountId": transaction["accountId"],
                "payeeName": transaction["payeeName"],
                "amount": _round_milliunits(transaction["amount"]),
                "date": transaction.get("date") or _today(),
                "categoryId": category_id,
            }
            if transaction.get("memo"):
                entry["memo"] = transaction["memo"]
            resolved.append(entry)
        return tool_result(
            client.post("/api/v1/ynab/transactions/bulk", {"transactions": resolved})
        )

    if action == "list_transactions":
        params: dict[str, str] = {}
        if input_data.get("date"):
            params["sinceDate"] = input_data["date"]
        if input_data.get("sinceDate"):
            params["sinceDate"] = input_data["sinceDate"]
        data = _convert_milliunits(
            client.get("/api/v1/ynab/transactions", params=params or None)
        )
        # Filter by untilDate client-side (API doesn't honor it)
        until_date = input_data.get("untilDate")
        if until_date and isinstance(data, dict) and isinstance(data.get("transactions"), list):
            data["transactions"] = [
                t for t in data["transactions"]
                if t.get("date", "") <= until_date
            ]
        # Apply limit and truncation
        from mind_your_now.tools import truncate
        if input_data.get("limit"):
            data = truncate(data, "transactions", int(input_data["limit"]))
        return tool_result(data)

    if action == "update_transaction":
        transaction_id = input_data.get("transactionId")
        if not transaction_id:
            return tool_error("transactionId is required for update_transaction.")
        body = {}
        for field in ("accountId", "payeeName", "date", "memo", "cleared", "flagColor"):
            if input_data.get(field):
                body[field] = input_data[field]
        if input_data.get("amount") is not None:
            body["amountMilliunits"] = _round_milliunits(input_data["amount"])
        if input_data.get("payeeId"):
            body["payeeId"] = input_data["payeeId"]
        elif input_data.get("transferToAccount"):
            payee_id, _ = _find_transfer_payee(client, input_data["transferToAccount"])
            if not payee_id:
                return tool_error(
                    f"Transfer target account '{input_data['transferToAccount']}' not found or has no transfer payee ID."
                )
            body["payeeId"] = payee_id
        if input_data.get("categoryName"):
            category_id = _resolve_category_id(client, input_data["categoryName"])
            if not category_id:
                return tool_error(f"Category '{input_data['categoryName']}' not found.")
            body["categoryId"] = category_id
        return tool_result(
            _convert_milliunits(
                client.put(f"/api/v1/ynab/transactions/{transaction_id}", body)
            )
        )

    if action == "delete_transaction":
        transaction_id = input_data.get("transactionId")
        if not transaction_id:
            return tool_error("transactionId is required for delete_transaction.")
        return tool_result(
            _convert_milliunits(
                client.delete(f"/api/v1/ynab/transactions/{transaction_id}")
            )
        )

    if action == "split_transaction":
        transaction_id = input_data.get("transactionId")
        splits = input_data.get("splits")
        if not transaction_id:
            return tool_error(
                "transactionId is required for split_transaction. Use list_transactions to find the transaction."
            )
        if not splits or len(splits) < 2:
            return tool_error(
                "splits array with at least 2 entries is required for split_transaction. Each entry needs categoryName and amount."
            )
        amounts = [_round_milliunits(split["amount"]) for split in splits]
        since_date = (datetime.now(timezone.utc).date() - timedelta(days=90)).isoformat()
        transactions = client.get(
            "/api/v1/ynab/transactions", params={"sinceDate": since_date}
        )
        original = next(
            (
                item
                for item in (
                    transactions.get("transactions", [])
                    if isinstance(transactions, dict)
                    else []
                )
                if item.get("id") == transaction_id
            ),
            None,
        )
        if original and sum(amounts) != original.get("amount"):
            return tool_error(
                "Split amounts must sum to the original transaction amount. "
                f"Splits sum to ${sum(amounts) / 1000:.2f} but original is ${original.get('amount', 0) / 1000:.2f}. "
                "Adjust the split amounts so they add up exactly."
            )
        subtransactions = []
        for split, amount in zip(splits, amounts, strict=True):
            category_id = _resolve_category_id(client, split["categoryName"])
            if not category_id:
                return tool_error(
                    f"Category '{split['categoryName']}' not found. Use list_categories to browse."
                )
            entry = {"amount": amount, "categoryId": category_id}
            if split.get("memo"):
                entry["memo"] = split["memo"]
            subtransactions.append(entry)
        return tool_result(
            _convert_milliunits(
                client.put(
                    f"/api/v1/ynab/transactions/{transaction_id}",
                    {"subtransactions": subtransactions},
                )
            )
        )

    if action == "create_scheduled_transaction":
        for field, message in (
            ("accountId", "accountId is required for create_scheduled_transaction."),
            ("payeeName", "payeeName is required for create_scheduled_transaction."),
            ("frequency", "frequency is required (e.g., monthly, weekly, yearly)."),
            ("dateFirst", "dateFirst is required (YYYY-MM-DD, first occurrence date)."),
        ):
            if not input_data.get(field):
                return tool_error(message)
        if input_data.get("amount") is None:
            return tool_error(
                "amount is required for create_scheduled_transaction (in dollars)."
            )
        category_id = None
        if input_data.get("categoryName"):
            category_id = _resolve_category_id(client, input_data["categoryName"])
            if not category_id:
                return tool_error(f"Category '{input_data['categoryName']}' not found.")
        body = {
            "accountId": input_data["accountId"],
            "payeeName": input_data["payeeName"],
            "amountMilliunits": _round_milliunits(input_data["amount"]),
            "dateFirst": input_data["dateFirst"],
            "dateNext": input_data["dateFirst"],
            "frequency": input_data["frequency"],
        }
        if input_data.get("memo"):
            body["memo"] = input_data["memo"]
        if category_id:
            body["categoryId"] = category_id
        return tool_result(
            _convert_milliunits(
                client.post("/api/v1/ynab/scheduled-transactions", body)
            )
        )

    if action == "update_scheduled_transaction":
        transaction_id = input_data.get("transactionId")
        if not transaction_id:
            return tool_error(
                "transactionId is required for update_scheduled_transaction."
            )
        body = {}
        mapping = {
            "payeeName": "payeeName",
            "date": "dateNext",
            "frequency": "frequency",
            "memo": "memo",
        }
        for source, destination in mapping.items():
            if input_data.get(source):
                body[destination] = input_data[source]
        if input_data.get("amount") is not None:
            body["amountMilliunits"] = _round_milliunits(input_data["amount"])
        if input_data.get("categoryName"):
            category_id = _resolve_category_id(client, input_data["categoryName"])
            if not category_id:
                return tool_error(f"Category '{input_data['categoryName']}' not found.")
            body["categoryId"] = category_id
        return tool_result(
            _convert_milliunits(
                client.put(
                    f"/api/v1/ynab/scheduled-transactions/{transaction_id}", body
                )
            )
        )

    if action == "delete_scheduled_transaction":
        transaction_id = input_data.get("transactionId")
        if not transaction_id:
            return tool_error(
                "transactionId is required for delete_scheduled_transaction."
            )
        return tool_result(
            client.delete(f"/api/v1/ynab/scheduled-transactions/{transaction_id}")
        )

    analytics = {
        "upcoming_bills": ("/api/v1/ynab/scheduled", {"days": input_data.get("days") or 7}),
        "spending_insights": (
            "/api/v1/ynab/analytics/spending",
            {
                "months": input_data.get("months") or 3,
                **(
                    {"category": input_data["categoryName"]}
                    if input_data.get("categoryName")
                    else {}
                ),
            },
        ),
        "payee_analysis": (
            "/api/v1/ynab/analytics/payees",
            {"months": input_data.get("months") or 3},
        ),
        "spending_trends": (
            "/api/v1/ynab/analytics/trends",
            {"months": input_data.get("months") or 6},
        ),
    }
    if action in analytics:
        path, params = analytics[action]
        return tool_result(_convert_milliunits(client.get(path, params=params)))

    if action == "create_category_group":
        if not input_data.get("groupName"):
            return tool_error("groupName is required for create_category_group.")
        return tool_result(
            client.post(
                "/api/v1/ynab/budget/category-groups",
                {"name": input_data["groupName"]},
            )
        )

    if action == "create_category":
        if not input_data.get("categoryName"):
            return tool_error("categoryName is required for create_category.")
        group_id = input_data.get("categoryGroupId")
        if not group_id and input_data.get("groupName"):
            group = _resolve_category_group(client, input_data["groupName"])
            if not group:
                return tool_error(
                    f"Category group '{input_data['groupName']}' not found. Use list_categories to see available groups, or create_category_group to create one."
                )
            group_id = group["id"]
        if not group_id:
            return tool_error(
                "groupName or categoryGroupId is required for create_category."
            )
        body = {
            "name": input_data["categoryName"],
            "categoryGroupId": group_id,
        }
        if input_data.get("note"):
            body["note"] = input_data["note"]
        return tool_result(client.post("/api/v1/ynab/budget/categories", body))

    if action == "rename_category":
        if not input_data.get("categoryName"):
            return tool_error(
                "categoryName is required (current name to find the category)."
            )
        if not input_data.get("newName"):
            return tool_error("newName is required for rename_category.")
        category_id = _resolve_category_id(client, input_data["categoryName"])
        if not category_id:
            return tool_error(f"Category '{input_data['categoryName']}' not found.")
        return tool_result(
            client.patch(
                f"/api/v1/ynab/budget/categories/{category_id}/details",
                {"name": input_data["newName"]},
            )
        )

    if action == "move_category":
        if not input_data.get("categoryName"):
            return tool_error("categoryName is required (category to move).")
        if not input_data.get("targetGroupName") and not input_data.get("categoryGroupId"):
            return tool_error(
                "targetGroupName or categoryGroupId is required (destination group)."
            )
        category_id = _resolve_category_id(client, input_data["categoryName"])
        if not category_id:
            return tool_error(f"Category '{input_data['categoryName']}' not found.")
        group_id = input_data.get("categoryGroupId")
        if not group_id:
            group = _resolve_category_group(client, input_data["targetGroupName"])
            if not group:
                return tool_error(
                    f"Category group '{input_data['targetGroupName']}' not found."
                )
            group_id = group["id"]
        return tool_result(
            client.patch(
                f"/api/v1/ynab/budget/categories/{category_id}/details",
                {"categoryGroupId": group_id},
            )
        )

    if action == "rename_category_group":
        if not input_data.get("groupName"):
            return tool_error("groupName is required (current group name).")
        if not input_data.get("newName"):
            return tool_error("newName is required for rename_category_group.")
        group = _resolve_category_group(client, input_data["groupName"])
        if not group:
            return tool_error(f"Category group '{input_data['groupName']}' not found.")
        return tool_result(
            client.patch(
                f"/api/v1/ynab/budget/category-groups/{group['id']}",
                {"name": input_data["newName"]},
            )
        )

    return tool_error(f"Unknown action: {action}")


def register_ynab_tool(
    ctx: Any,
    client: MynApiClient,
    check_fn: Callable[[], bool],
) -> None:
    register_myn_tool(
        ctx,
        name="myn_ynab",
        schema=YNAB_SCHEMA,
        handler=lambda **kwargs: execute_ynab(client, **kwargs),
        check_fn=check_fn,
        description=(
            "YNAB budget management with full read/write access. Budget, transactions, "
            "scheduled transactions, analytics, category management, and connection status. "
            "Amounts use dollars; categories are resolved by name. categoryName is required "
            "for non-transfer transactions."
        ),
        emoji="💰",
    )
