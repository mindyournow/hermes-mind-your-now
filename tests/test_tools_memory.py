import json
import sys
import types

import httpx
import pytest

from mind_your_now.client import MynApiClient
from mind_your_now.tools.memory import register_memory_tool


MEMORY_ID = "11111111-1111-4111-8111-111111111111"


def memory_dto(memory_id, content):
    return {
        "id": memory_id,
        "type": "PREFERENCE",
        "content": content,
        "confidence": 0.9,
        "sourceConversationId": "conversation-1",
        "sourceGoalId": None,
        "createdAt": "2026-03-01T10:00:00Z",
        "lastReinforcedAt": None,
        "reinforcementCount": 1,
        "lastUsedAt": None,
        "usageCount": 0,
        "topics": ["preference"],
        "hasEmbedding": True,
        "confidenceLevel": "high",
    }


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
            {
                "memories": [],
                "totalCount": 0,
                "limit": 50,
                "offset": 0,
                "hasMore": False,
            },
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


def test_recall_sends_limit_and_unwraps_memories_envelope():
    observed_params = None
    memories = [memory_dto(MEMORY_ID, "Remembered")]

    def transport(request):
        nonlocal observed_params
        observed_params = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "memories": memories,
                "totalCount": 1,
                "limit": 7,
                "offset": 0,
                "hasMore": False,
            },
        )

    result = json.loads(build_handler(transport)(action="recall", limit=7))

    assert observed_params == {"limit": "7"}
    assert result["data"]["memories"] == memories
    assert result["data"]["totalCount"] == 1


def test_recall_filters_by_memory_id_after_bounded_fetch():
    observed_params = None

    def transport(request):
        nonlocal observed_params
        observed_params = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "memories": [
                    memory_dto(MEMORY_ID, "Match"),
                    memory_dto("other", "Other"),
                ],
                "totalCount": 2,
                "limit": 50,
                "offset": 0,
                "hasMore": False,
            },
        )

    result = json.loads(
        build_handler(transport)(action="recall", memoryId=MEMORY_ID)
    )

    assert observed_params == {"limit": "200", "offset": "0"}
    assert result["data"] == memory_dto(MEMORY_ID, "Match")


def test_recall_by_id_scans_later_pages_ignoring_list_limit():
    observed_params = []

    def transport(request):
        params = dict(request.url.params)
        observed_params.append(params)
        offset = int(params["offset"])
        return httpx.Response(
            200,
            json={
                "memories": [
                    memory_dto(
                        "other" if offset == 0 else MEMORY_ID,
                        "Other" if offset == 0 else "Match",
                    )
                ],
                "totalCount": 2,
                "limit": 200,
                "offset": offset,
                "hasMore": offset == 0,
            },
        )

    result = json.loads(
        build_handler(transport)(action="recall", memoryId=MEMORY_ID, limit=1)
    )

    assert observed_params == [
        {"limit": "200", "offset": "0"},
        {"limit": "200", "offset": "1"},
    ]
    assert result["data"] == memory_dto(MEMORY_ID, "Match")


def test_recall_by_id_handles_wrapped_response():
    """Recall by ID reads the memory from the paginated response envelope."""
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


def test_recall_by_id_matches_legacy_memory_id_field():
    memory_id = "mem-456"

    def transport(request):
        return httpx.Response(
            200,
            json=[
                {"memoryId": memory_id, "content": "Found"},
                {"memoryId": "other", "content": "Not this"},
            ],
        )

    result = json.loads(build_handler(transport)(action="recall", memoryId=memory_id))
    assert result["data"] == {"memoryId": memory_id, "content": "Found"}


def test_recall_by_id_handles_bare_list():
    """Recall accepts bare list responses for compatibility."""
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
