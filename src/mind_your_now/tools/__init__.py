"""Shared registration and error handling for Mind Your Now tools."""

from __future__ import annotations

import functools
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mind_your_now.client import MynApiError


logger = logging.getLogger(__name__)

# MIN-930: Recursive redaction pattern for secret-shaped keys.
# Includes common HTTP/session credential names plus MYN-specific agent keys.
_SECRET_KEY_PATTERN = re.compile(
    r"(?i)(?:^token$|^authorization$|^cookie$|(?:access|refresh|id|session)_?token|secret|api_?key|password|credential|myn_?inbound_?key|agent_?key)"
)


def _redact_secrets(obj: Any) -> Any:
    """Recursively redact values whose keys match secret patterns. MIN-930 backstop."""
    if isinstance(obj, dict):
        return {
            key: "[REDACTED]" if _SECRET_KEY_PATTERN.search(str(key)) else _redact_secrets(value)
            for key, value in obj.items()
        }
    elif isinstance(obj, list):
        return [_redact_secrets(item) for item in obj]
    else:
        return obj


UNIFIED_TASK_PAGE_SIZE = 200
UNIFIED_TASK_MAX_PAGES = 50
UNIFIED_TASK_MAX_ITEMS = UNIFIED_TASK_PAGE_SIZE * UNIFIED_TASK_MAX_PAGES


@dataclass(frozen=True)
class UnifiedTaskScan:
    tasks: list[dict[str, Any]]
    complete: bool
    scanned_items: int
    pages: int


def fetch_all_unified_tasks(
    client: Any,
    *,
    params: dict[str, Any] | None = None,
    match_fn: Callable[[dict[str, Any]], bool] | None = None,
    stop_after: int | None = None,
) -> UnifiedTaskScan | Any:
    """Scan stable task pages with caller-aware stopping and a hard safety cap.

    The API requires a limit, caps it at 200, and returns a snapshot token that binds
    later offsets to the first page's collection generation. Matching results are
    deduplicated by task ID. A valid scan never exceeds 50 pages or 10,000 source
    items; callers receive ``complete=False`` when early stopping or the cap applies.
    Unexpected first-page shapes remain backward compatible.
    """
    offset = 0
    snapshot: str | None = None
    tasks: list[dict[str, Any]] = []
    seen_task_ids: set[str] = set()
    seen_page_signatures: set[tuple[str, ...]] = set()
    scanned_items = 0
    pages = 0

    def page_tasks(data: Any) -> list[dict[str, Any]] | None:
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("tasks"), list):
            return data["tasks"]
        return None

    def signature(items: list[dict[str, Any]]) -> tuple[str, ...]:
        return tuple(
            str(task.get("id", f"missing-id-{index}"))
            for index, task in enumerate(items)
        )

    while True:
        page_params = {
            **(params or {}),
            "limit": UNIFIED_TASK_PAGE_SIZE,
            "offset": offset,
        }
        if snapshot is not None:
            page_params["snapshot"] = snapshot

        data = client.get("/api/v2/unified-tasks", params=page_params)
        current_page = page_tasks(data)
        if current_page is None:
            if offset == 0:
                return data
            raise RuntimeError("Unified-task pagination returned an unexpected response shape")

        current_signature = signature(current_page)
        if current_signature in seen_page_signatures:
            raise RuntimeError("Unified-task pagination did not advance")
        seen_page_signatures.add(current_signature)
        pages += 1
        scanned_items += len(current_page)

        response_snapshot = data.get("snapshot") if isinstance(data, dict) else None
        if offset == 0:
            snapshot = response_snapshot
        elif snapshot is not None and response_snapshot != snapshot:
            raise RuntimeError("Unified-task collection changed during pagination")

        for task in current_page:
            task_id = task.get("id")
            if task_id is not None:
                task_key = str(task_id)
                if task_key in seen_task_ids:
                    continue
                seen_task_ids.add(task_key)
            if match_fn is None or match_fn(task):
                tasks.append(task)

        has_more = (
            bool(data.get("hasMore"))
            if isinstance(data, dict) and "hasMore" in data
            else len(current_page) == UNIFIED_TASK_PAGE_SIZE
        )
        if not has_more:
            return UnifiedTaskScan(tasks, True, scanned_items, pages)
        if not current_page:
            raise RuntimeError("Unified-task pagination did not advance")
        if isinstance(data, dict) and snapshot is None:
            raise RuntimeError("Unified-task pagination omitted its snapshot token")
        if stop_after is not None and len(tasks) >= stop_after:
            return UnifiedTaskScan(tasks, False, scanned_items, pages)
        if pages >= UNIFIED_TASK_MAX_PAGES or scanned_items >= UNIFIED_TASK_MAX_ITEMS:
            return UnifiedTaskScan(tasks, False, scanned_items, pages)
        offset += len(current_page)


def truncate(payload: dict[str, Any], key: str, limit: int, *, offset: int = 0) -> dict[str, Any]:
    """Apply client-side limit/offset to a list in the payload and mark if truncated.

    When the list is cut by limit or offset, sets _truncated: true and _totalCount to
    the pre-cut length. Silent truncation is worse than none — the markers are mandatory
    so the model knows it's looking at a slice.

    Args:
        payload: The response dict containing the list to truncate
        key: The key in payload whose value is the list to slice
        limit: Maximum number of items to return
        offset: Number of items to skip from the start (default 0)

    Returns:
        The payload with the list sliced and truncation markers added if necessary
    """
    items = payload.get(key, [])
    if not isinstance(items, list):
        return payload

    total_count = len(items)
    start = offset
    end = offset + limit
    sliced = items[start:end]

    # Only modify if we actually cut something
    if len(sliced) < total_count:
        return {
            **payload,
            key: sliced,
            "_truncated": True,
            "_totalCount": total_count,
        }

    return payload


def tool_result(payload: Any) -> str:
    from tools.registry import tool_result as hermes_tool_result

    # MIN-930: Redact secret-shaped keys from the payload before returning to Hermes.
    # This is a backstop against API regressions; server-side gates are the primary defense.
    redacted = _redact_secrets(payload)
    return hermes_tool_result(redacted)


def tool_error(message: str) -> str:
    from tools.registry import tool_error as hermes_tool_error

    return hermes_tool_error(message)


def guarded(
    available: Callable[..., bool],
    fn: Callable[..., str],
) -> Callable[..., str]:
    """Self-guard a handler because Hermes dispatch ignores check_fn."""

    @functools.wraps(fn)
    def wrapper(
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        if not available():
            return tool_error("MYN not configured — set MYN_API_KEY")
        if arguments is not None and not isinstance(arguments, dict):
            return tool_error("MYN tool arguments must be an object")
        payload = {**(arguments or {}), **kwargs}
        try:
            return fn(**payload)
        except MynApiError as exc:
            return tool_error(f"MYN API {exc.status}: {exc.snippet}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[myn] %s failed: %s", fn.__name__, exc)
            return tool_error(f"MYN tool failure: {exc}")

    return wrapper


def register_myn_tool(
    ctx: Any,
    *,
    name: str,
    schema: dict[str, Any],
    handler: Callable[..., str],
    check_fn: Callable[..., bool],
    description: str,
    emoji: str,
) -> None:
    """Register one guarded handler in the shared MYN toolset."""
    # Wrap the bare JSON Schema parameters in a complete OpenAI function object
    # because hermes-agent's registry emits entry.schema verbatim as the function spec.
    wrapped_schema = {
        "name": name,
        "description": description,
        "parameters": schema,
    }
    ctx.register_tool(
        name=name,
        toolset="mind-your-now",
        schema=wrapped_schema,
        handler=guarded(check_fn, handler),
        check_fn=check_fn,
        description=description,
        emoji=emoji,
    )
