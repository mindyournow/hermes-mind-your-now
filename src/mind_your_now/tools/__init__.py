"""Shared registration and error handling for Mind Your Now tools."""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import Any

from mind_your_now.client import MynApiError


logger = logging.getLogger(__name__)


def tool_result(payload: Any) -> str:
    from tools.registry import tool_result as hermes_tool_result

    return hermes_tool_result(payload)


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
