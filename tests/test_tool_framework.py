import logging
import sys
import types

import pytest

from mind_your_now.client import MynApiError
from mind_your_now.schemas import action_schema
from mind_your_now.tools import guarded, register_myn_tool


@pytest.fixture(autouse=True)
def fake_hermes_registry(monkeypatch):
    tools_module = types.ModuleType("tools")
    registry_module = types.ModuleType("tools.registry")
    registry_module.tool_error = lambda message: f"ERROR: {message}"
    tools_module.registry = registry_module
    monkeypatch.setitem(sys.modules, "tools", tools_module)
    monkeypatch.setitem(sys.modules, "tools.registry", registry_module)


def test_guard_returns_tool_error_when_unavailable():
    called = False

    def handler(**_kwargs):
        nonlocal called
        called = True
        return "unexpected"

    result = guarded(lambda: False, handler)(action="list")

    assert result == "ERROR: MYN not configured — set MYN_API_KEY"
    assert called is False


def test_guard_accepts_hermes_positional_argument_object():
    received = None

    def handler(**kwargs):
        nonlocal received
        received = kwargs
        return "ok"

    result = guarded(lambda: True, handler)({"action": "list", "limit": 1})

    assert result == "ok"
    assert received == {"action": "list", "limit": 1}


def test_guard_maps_api_error():
    def handler(**_kwargs):
        raise MynApiError(404, "missing")

    result = guarded(lambda: True, handler)(action="get")

    assert result == "ERROR: MYN API 404: missing"


def test_guard_logs_unexpected_exception_at_warning(caplog):
    def handler(**_kwargs):
        raise RuntimeError("boom")

    with caplog.at_level(logging.WARNING, logger="mind_your_now.tools"):
        result = guarded(lambda: True, handler)(action="list")

    assert result == "ERROR: MYN tool failure: boom"
    assert "[myn] handler failed: boom" in caplog.text


def test_register_myn_tool_passes_guard_and_check_fn():
    class Context:
        def __init__(self):
            self.kwargs = None

        def register_tool(self, **kwargs):
            self.kwargs = kwargs

    context = Context()
    check_fn = lambda: False

    register_myn_tool(
        context,
        name="myn_example",
        schema={"type": "object"},
        handler=lambda **_kwargs: "ok",
        check_fn=check_fn,
        description="Example",
        emoji="🧭",
    )

    assert context.kwargs["name"] == "myn_example"
    assert context.kwargs["toolset"] == "mind-your-now"
    assert context.kwargs["check_fn"] is check_fn
    assert context.kwargs["handler"]() == (
        "ERROR: MYN not configured — set MYN_API_KEY"
    )


def test_action_schema_requires_action_and_declared_fields():
    schema = action_schema(
        ["list", "get"],
        {"id": {"type": "string"}},
        ["id"],
    )

    assert schema == {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "get"]},
            "id": {"type": "string"},
        },
        "required": ["action", "id"],
    }
