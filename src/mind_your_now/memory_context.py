"""Fetch and format Kaia memories for Hermes user-message context."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from mind_your_now.behavioral_guidelines import BEHAVIORAL_GUIDELINES
from mind_your_now.client import MynApiClient


logger = logging.getLogger(__name__)


def fetch_memory_context(
    client: MynApiClient,
    query: str,
    limit: int = 10,
) -> list[dict[str, Any]] | None:
    """Return memories relevant to the current user message."""
    params: dict[str, Any] = {"limit": limit}
    if query:
        params["query"] = query
    response = client.get("/api/v1/agent/memories/context", params=params)
    if not isinstance(response, dict):
        return None
    items = response.get("items")
    if not isinstance(items, list) or not items:
        return None
    return items


def format_memories_for_prompt(memories: list[dict[str, Any]]) -> str:
    """Format memories as a self-introducing user-message context block."""
    if not memories:
        return ""

    lines = []
    for memory in memories:
        confidence = float(memory["confidence"])
        percentage = int(confidence * 100 + 0.5)
        lines.append(
            f"- [{memory['type']}] {memory['content']} "
            f"(confidence: {percentage}%)"
        )

    return "\n".join(
        [
            "## What you know about this user (from Kaia Memory)",
            "",
            *lines,
            "",
            "Use these memories naturally in conversation. Do not explicitly "
            "cite confidence scores to the user.",
        ]
    )


def build_pre_llm_call_hook(
    client: MynApiClient,
    api_key: str | None,
    *,
    log: logging.Logger = logger,
) -> Callable[..., dict[str, str] | None]:
    """Build Hermes's non-blocking pre_llm_call memory hook."""

    def on_pre_llm_call(
        *,
        conversation_history: Any = None,
        user_message: str = "",
        **_kwargs: Any,
    ) -> dict[str, str] | None:
        del conversation_history
        if not api_key:
            return None
        try:
            memories = fetch_memory_context(client, user_message)
            if not memories:
                return None
            block = format_memories_for_prompt(memories)
            if not block:
                return None
            return {"context": f"{BEHAVIORAL_GUIDELINES}\n{block}"}
        except Exception as exc:  # noqa: BLE001
            log.warning("[myn] memory injection failed: %s", exc)
            return None

    return on_pre_llm_call
