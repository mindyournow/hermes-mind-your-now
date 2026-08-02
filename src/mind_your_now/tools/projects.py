"""myn_projects: browse fixed collections and file tasks into them."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mind_your_now.client import MynApiClient
from mind_your_now.schemas import action_schema
from mind_your_now.tools import register_myn_tool, tool_error, tool_result


PROJECTS_SCHEMA = action_schema(
    ["list", "get", "move_task"],
    {
        "projectId": {"type": "string", "format": "uuid"},
        "taskId": {"type": "string", "format": "uuid"},
        "targetProjectId": {"type": "string", "format": "uuid"},
        "limit": {"type": "number", "description": "Maximum collections to return"},
    },
)


def execute_projects(client: MynApiClient, **input_data: Any) -> str:
    action = input_data.get("action")

    if action == "list":
        limit = int(input_data.get("limit", 50))
        data = client.get("/api/project/defaults", params={"limit": limit})

        if isinstance(data, list):
            projects = data
        elif isinstance(data, dict) and isinstance(data.get("projects"), list):
            projects = data["projects"]
        else:
            return tool_result(data)

        from mind_your_now.tools import truncate
        result = {"projects": projects}
        if input_data.get("limit"):
            result = truncate(result, "projects", limit)

        return tool_result(result)

    if action == "get":
        project_id = input_data.get("projectId")
        if not project_id:
            return tool_error("projectId is required for get action")
        return tool_result(client.get(f"/api/project/{project_id}"))

    if action == "move_task":
        task_id = input_data.get("taskId")
        target_project_id = input_data.get("targetProjectId")
        if not task_id:
            return tool_error("taskId is required for move_task action")
        if not target_project_id:
            return tool_error("targetProjectId is required for move_task action")
        return tool_result(
            client.put(
                f"/api/project/{target_project_id}/moveTaskToProject/{task_id}"
            )
        )

    return tool_error(f"Unknown action: {action}")


def register_projects_tool(
    ctx: Any,
    client: MynApiClient,
    check_fn: Callable[[], bool],
) -> None:
    register_myn_tool(
        ctx,
        name="myn_projects",
        schema=PROJECTS_SCHEMA,
        handler=lambda **kwargs: execute_projects(client, **kwargs),
        check_fn=check_fn,
        description=(
            'Browse MYN collections (called "projects" in the API) and file tasks into them. '
            "MYN has a fixed set of collections — PERSONAL, WORK, GROCERIES, BOOKS, CHORES, "
            "and so on. They cannot be created, renamed, or deleted; use move_task to change "
            "which collection a task belongs to. Actions: list, get, move_task."
        ),
        emoji="📁",
    )
