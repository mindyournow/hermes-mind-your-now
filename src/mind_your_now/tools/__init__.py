"""Shared registration and error handling for Mind Your Now tools."""

from __future__ import annotations

import functools
import json
import logging
import re
from collections.abc import Callable
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


def fetch_all_unified_tasks(
    client: Any,
    *,
    params: dict[str, Any] | None = None,
) -> Any:
    """Retrieve every page from the unified-task endpoint.

    The API defaults to 50 records and caps pages at 200. Client-side filters and
    pagination must operate on the complete collection, not the first server page.
    Unexpected first-page shapes are returned unchanged for backward compatibility.
    """
    page_size = 200
    page = 0
    tasks: list[dict[str, Any]] = []
    seen_page_signatures: set[tuple[str, ...]] = set()

    while True:
        page_params = {**(params or {}), "page": page, "size": page_size}
        data = client.get("/api/v2/unified-tasks", params=page_params)
        if isinstance(data, list):
            page_tasks = data
        elif isinstance(data, dict) and isinstance(data.get("tasks"), list):
            page_tasks = data["tasks"]
        elif page == 0:
            return data
        else:
            raise RuntimeError("Unified-task pagination returned an unexpected response shape")

        signature = tuple(
            str(task.get("id", f"missing-id-{index}"))
            for index, task in enumerate(page_tasks)
        )
        if signature in seen_page_signatures:
            raise RuntimeError("Unified-task pagination did not advance")
        seen_page_signatures.add(signature)
        tasks.extend(page_tasks)

        if len(page_tasks) < page_size:
            return tasks
        page += 1


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
