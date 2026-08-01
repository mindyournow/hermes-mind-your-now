"""myn_projects: project and category management."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mind_your_now.client import MynApiClient
from mind_your_now.schemas import action_schema
from mind_your_now.tools import register_myn_tool, tool_error, tool_result


PROJECTS_SCHEMA = action_schema(
    ["list", "get", "create", "move_task"],
    {
        "projectId": {"type": "string", "format": "uuid"},
        "name": {"type": "string", "minLength": 1, "maxLength": 100},
        "description": {"type": "string", "maxLength": 500},
        "color": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
        "icon": {"type": "string"},
        "parentProjectId": {"type": "string", "format": "uuid"},
        "taskId": {"type": "string", "format": "uuid"},
        "targetProjectId": {"type": "string", "format": "uuid"},
        "includeArchived": {"type": "boolean", "default": False},
        "includeStats": {"type": "boolean", "default": True},
    },
)


def execute_projects(client: MynApiClient, **input_data: Any) -> str:
    action = input_data.get("action")

    if action == "list":
        params = {"limit": 50}
        if input_data.get("includeArchived"):
            params["includeArchived"] = "true"
        if input_data.get("includeStats"):
            params["includeStats"] = "true"
        data = client.get("/api/project/defaults", params=params)
        projects = data.get("projects", []) if isinstance(data, dict) else []
        return tool_result(projects)

    if action == "get":
        project_id = input_data.get("projectId")
        if not project_id:
            return tool_error("projectId is required for get action")
        return tool_result(client.get(f"/api/project/{project_id}"))

    if action == "create":
        if not input_data.get("name"):
            return tool_error("name is required for create action")
        body = {"name": input_data["name"]}
        mapping = {
            "description": "description",
            "color": "color",
            "icon": "icon",
            "parentProjectId": "parentId",
        }
        for source, destination in mapping.items():
            if input_data.get(source):
                body[destination] = input_data[source]
        return tool_result(client.post("/api/project/create", body))

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
        description="Manage projects and categories. Actions: list, get, create, move_task.",
        emoji="📁",
    )
