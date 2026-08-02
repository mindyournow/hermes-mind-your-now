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


def test_recall_by_id_uses_customer_owned_direct_lookup():
    observed = None

    def transport(request):
        nonlocal observed
        observed = (request.url.path, dict(request.url.params))
        return httpx.Response(200, json=memory_dto(MEMORY_ID, "Match"))

    result = json.loads(
        build_handler(transport)(action="recall", memoryId=MEMORY_ID)
    )

    assert observed == (
        f"/api/v1/customers/memories/{MEMORY_ID}",
        {},
    )
    assert result["data"] == memory_dto(MEMORY_ID, "Match")


def test_recall_by_id_ignores_the_list_limit():
    requests = 0

    def transport(request):
        nonlocal requests
        requests += 1
        assert dict(request.url.params) == {}
        return httpx.Response(200, json=memory_dto(MEMORY_ID, "Match"))

    result = json.loads(
        build_handler(transport)(action="recall", memoryId=MEMORY_ID, limit=1)
    )

    assert requests == 1
    assert result["data"] == memory_dto(MEMORY_ID, "Match")


def test_recall_by_id_rejects_a_mismatched_direct_response():
    other_id = "22222222-2222-4222-8222-222222222222"

    def transport(request):
        return httpx.Response(200, json=memory_dto(other_id, "Other"))

    result = json.loads(
        build_handler(transport)(action="recall", memoryId=MEMORY_ID)
    )

    assert result == {
        "success": False,
        "error": "Memory lookup returned an unexpected id",
    }


def test_recall_by_id_returns_stable_not_found_error():
    def transport(request):
        return httpx.Response(404, json={"message": "Memory not found"})

    result = json.loads(
        build_handler(transport)(action="recall", memoryId=MEMORY_ID)
    )

    assert result == {
        "success": False,
        "error": f"Memory not found: {MEMORY_ID}",
    }


def test_recall_by_id_matches_legacy_memory_id_field():
    memory_id = "mem-456"

    def transport(request):
        return httpx.Response(
            200,
            json={"memoryId": memory_id, "content": "Found"},
        )

    result = json.loads(build_handler(transport)(action="recall", memoryId=memory_id))
    assert result["data"] == {"memoryId": memory_id, "content": "Found"}


def test_recall_by_id_rejects_a_non_object_response():
    def transport(request):
        return httpx.Response(200, json=[])

    result = json.loads(
        build_handler(transport)(action="recall", memoryId=MEMORY_ID)
    )

    assert result == {
        "success": False,
        "error": "Memory lookup returned an unexpected id",
    }
