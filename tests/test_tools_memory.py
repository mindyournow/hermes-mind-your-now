import json
import sys
import types

import httpx
import pytest

from mind_your_now.client import MynApiClient
from mind_your_now.tools.memory import register_memory_tool


MEMORY_ID = "11111111-1111-4111-8111-111111111111"


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


def build_handler(transport):
    client = MynApiClient(
        "https://api.example.com",
        "myn-key",
        transport=httpx.MockTransport(transport),
    )
    context = Context()
    register_memory_tool(context, client, lambda: True)
    return context.registration["handler"]


@pytest.mark.parametrize(
    ("input_data", "method", "path", "response"),
    [
        (
            {"action": "remember", "content": "Likes morning meetings"},
            "POST",
            "/api/v1/agent/memories",
            {"id": MEMORY_ID},
        ),
        (
            {"action": "recall"},
            "GET",
            "/api/v1/customers/memories",
            [],
        ),
        (
            {"action": "forget", "memoryId": MEMORY_ID},
            "DELETE",
            f"/api/v1/customers/memories/{MEMORY_ID}",
            None,
        ),
        (
            {"action": "search", "query": "morning"},
            "GET",
            "/api/v1/agent/memories/context",
            {"items": [], "total": 0},
        ),
    ],
)
def test_actions_use_expected_methods_and_paths(
    input_data, method, path, response
):
    observed = []

    def transport(request):
        observed.append((request.method, request.url.path))
        return httpx.Response(204) if response is None else httpx.Response(200, json=response)

    result = json.loads(build_handler(transport)(**input_data))

    assert observed == [(method, path)]
    assert result["success"] is True


def test_memory_search_uses_context_endpoint_with_encoded_params():
    observed = {}
    payload = {"items": [{"id": MEMORY_ID, "content": "Bread & butter"}], "total": 1}

    def transport(request):
        observed["path"] = request.url.path
        observed["params"] = dict(request.url.params)
        observed["url"] = str(request.url)
        return httpx.Response(200, json=payload)

    result = json.loads(
        build_handler(transport)(
            action="search",
            query="bread & butter",
            limit=7,
        )
    )

    assert observed == {
        "path": "/api/v1/agent/memories/context",
        "params": {"query": "bread & butter", "limit": "7"},
        "url": "https://api.example.com/api/v1/agent/memories/context?query=bread+%26+butter&limit=7",
    }
    assert result == {"success": True, "data": payload}


def test_recall_filters_by_memory_id_after_bounded_fetch():
    observed_params = None

    def transport(request):
        nonlocal observed_params
        observed_params = dict(request.url.params)
        return httpx.Response(
            200,
            json=[
                {"memoryId": MEMORY_ID, "content": "Match"},
                {"memoryId": "other", "content": "Other"},
            ],
        )

    result = json.loads(
        build_handler(transport)(action="recall", memoryId=MEMORY_ID)
    )

    assert observed_params == {"limit": "50"}
    assert result["data"] == {"memoryId": MEMORY_ID, "content": "Match"}


def test_recall_by_id_handles_wrapped_response():
    """recall with memoryId returns the memory from wrapped {\"memories\": [...], \"totalCount\": N} response."""
    memory_id = "mem-123"

    def transport(request):
        return httpx.Response(
            200,
            json={
                "memories": [
                    {"id": memory_id, "content": "Match"},
                    {"id": "other", "content": "Other"},
                ],
                "totalCount": 2,
            },
        )

    result = json.loads(build_handler(transport)(action="recall", memoryId=memory_id))
    assert result["data"] == {"id": memory_id, "content": "Match"}


def test_recall_by_id_matches_on_id_field():
    """recall with memoryId matches on the 'id' field (not 'memoryId')."""
    memory_id = "mem-456"

    def transport(request):
        return httpx.Response(
            200,
            json=[
                {"id": memory_id, "content": "Found"},
                {"id": "other", "content": "Not this"},
            ],
        )

    result = json.loads(build_handler(transport)(action="recall", memoryId=memory_id))
    assert result["data"] == {"id": memory_id, "content": "Found"}


def test_recall_by_id_handles_bare_list():
    """recall accepts bare list responses for compatibility."""
    memory_id = "mem-789"

    def transport(request):
        return httpx.Response(
            200,
            json=[
                {"id": memory_id, "content": "Match"},
                {"id": "other", "content": "Other"},
            ],
        )

    result = json.loads(build_handler(transport)(action="recall", memoryId=memory_id))
    assert result["data"] == {"id": memory_id, "content": "Match"}
