"""Hermes plugin registration for Mind Your Now."""

from __future__ import annotations

import logging
from typing import Any

from mind_your_now.client import MynApiClient
from mind_your_now.commands import handle_myn_command
from mind_your_now.config import MynConfigError, load_config
from mind_your_now.memory_context import build_pre_llm_call_hook
from mind_your_now.tools.a2a_pairing import register_a2a_pairing_tool
from mind_your_now.tools.calendar import register_calendar_tool
from mind_your_now.tools.debrief import register_debrief_tool
from mind_your_now.tools.habits import register_habits_tool
from mind_your_now.tools.household import register_household_tool
from mind_your_now.tools.lists import register_lists_tool
from mind_your_now.tools.memory import register_memory_tool
from mind_your_now.tools.planning import register_planning_tool
from mind_your_now.tools.profile import register_profile_tool
from mind_your_now.tools.projects import register_projects_tool
from mind_your_now.tools.search import register_search_tool
from mind_your_now.tools.tasks import register_tasks_tool
from mind_your_now.tools.timers import register_timers_tool
from mind_your_now.tools.ynab import register_ynab_tool


__all__ = ["register"]


logger = logging.getLogger(__name__)


def _warn(ctx: Any, message: str) -> None:
    target = getattr(ctx, "logger", logger)
    if hasattr(target, "warning"):
        target.warning(message)
    else:
        target.warn(message)


def register(ctx: Any) -> None:
    """Register all MYN tools, hooks, and commands with Hermes."""
    try:
        config = load_config()
    except MynConfigError as exc:
        _warn(ctx, f"[myn] Invalid configuration: {exc}")
        return

    if not config.api_key:
        _warn(ctx, "[myn] MYN_API_KEY not configured; tools registered but hidden")

    client = MynApiClient(config.base_url, config.api_key)
    available = lambda: bool(config.api_key)
    provenance = {
        "source_agent_name": config.agent_name,
        "source_channel": config.channel,
    }

    register_tasks_tool(ctx, client, available)
    register_debrief_tool(ctx, client, available)
    register_calendar_tool(ctx, client, available)
    register_habits_tool(ctx, client, available)
    register_lists_tool(ctx, client, available)
    register_search_tool(ctx, client, available)
    register_timers_tool(ctx, client, available, provenance)
    register_memory_tool(ctx, client, available)
    register_profile_tool(ctx, client, available)
    register_household_tool(ctx, client, available)
    register_projects_tool(ctx, client, available)
    register_planning_tool(ctx, client, available)
    register_a2a_pairing_tool(ctx, config.base_url, available)
    register_ynab_tool(ctx, client, available)

    ctx.register_hook(
        "pre_llm_call",
        build_pre_llm_call_hook(client, config.api_key),
    )
    ctx.register_command(
        "myn",
        handler=lambda raw_args: handle_myn_command(raw_args, client, config),
        description="Mind Your Now controls — status, pair, unpair",
        args_hint="<status|pair INVITE|unpair>",
    )
