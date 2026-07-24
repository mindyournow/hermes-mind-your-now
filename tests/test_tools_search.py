import json
import sys
import types

import httpx
import pytest

from mind_your_now.client import MynApiClient
from mind_your_now.tools.search import register_search_tool


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


def test_search_uses_encoded_params_and_preserves_response():
    observed = {}
    payload = {"results": [{"id": "1", "type": "task"}], "total": 1}

    def transport(request):
        observed["path"] = request.url.path
        observed["query"] = request.url.params["q"]
        observed["types"] = request.url.params.get_list("types")
        observed["status"] = request.url.params["status"]
        observed["url"] = str(request.url)
        return httpx.Response(200, json=payload)

    client = MynApiClient(
        "https://api.example.com",
        "myn-key",
        transport=httpx.MockTransport(transport),
    )
    context = Context()
    register_search_tool(context, client, lambda: True)

    result = json.loads(
        context.registration["handler"](
            action="search",
            query="bread & butter",
            types=["task", "event"],
            filters={"status": "PENDING"},
            limit=20,
            offset=0,
        )
    )

    assert observed == {
        "path": "/api/v2/search",
        "query": "bread & butter",
        "types": ["task", "event"],
        "status": "PENDING",
        "url": "https://api.example.com/api/v2/search?q=bread+%26+butter&types=task&types=event&status=PENDING&limit=20&offset=0",
    }
    assert result == {"success": True, "data": payload}


def test_search_requires_query():
    client = MynApiClient(
        "https://api.example.com",
        "myn-key",
        transport=httpx.MockTransport(
            lambda _request: pytest.fail("request must not be sent")
        ),
    )
    context = Context()
    register_search_tool(context, client, lambda: True)

    result = json.loads(context.registration["handler"](action="search"))

    assert result == {
        "success": False,
        "error": "query is required for search action",
    }
