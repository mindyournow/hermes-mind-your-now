import json
import sys
import types

import pytest

from mind_your_now import register


EXPECTED_TOOLS = {
    "myn_tasks",
    "myn_debrief",
    "myn_calendar",
    "myn_habits",
    "myn_lists",
    "myn_search",
    "myn_timers",
    "myn_memory",
    "myn_profile",
    "myn_household",
    "myn_projects",
    "myn_planning",
    "myn_a2a_pairing",
    "myn_ynab",
}
ENV_KEYS = ["MYN_API_KEY", "MYN_BASE_URL", "MYN_AGENT_NAME", "MYN_CHANNEL"]


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


class Logger:
    def __init__(self):
        self.warnings = []

    def warning(self, message):
        self.warnings.append(message)


class Context:
    def __init__(self):
        self.logger = Logger()
        self.tools = []
        self.hooks = []
        self.commands = []

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)

    def register_hook(self, name, handler):
        self.hooks.append((name, handler))

    def register_command(self, name, **kwargs):
        self.commands.append((name, kwargs))


def clear_config(monkeypatch, home):
    monkeypatch.setenv("HOME", str(home))
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_registers_14_tools_one_hook_one_command(tmp_path, monkeypatch):
    clear_config(monkeypatch, tmp_path)
    monkeypatch.setenv("MYN_API_KEY", "myn-key")
    context = Context()

    register(context)

    assert {tool["name"] for tool in context.tools} == EXPECTED_TOOLS
    assert len(context.tools) == 14
    assert [name for name, _handler in context.hooks] == ["pre_llm_call"]
    assert [name for name, _kwargs in context.commands] == ["myn"]


def test_all_tools_in_myn_toolset(tmp_path, monkeypatch):
    clear_config(monkeypatch, tmp_path)
    monkeypatch.setenv("MYN_API_KEY", "myn-key")
    context = Context()

    register(context)

    assert {tool["toolset"] for tool in context.tools} == {"mind-your-now"}


def test_handlers_error_cleanly_without_key(tmp_path, monkeypatch):
    clear_config(monkeypatch, tmp_path)
    context = Context()

    register(context)

    assert len(context.tools) == 14
    for tool in context.tools:
        result = json.loads(tool["handler"]())
        assert result == {
            "success": False,
            "error": "MYN not configured — set MYN_API_KEY",
        }
        assert tool["check_fn"]() is False
    assert context.logger.warnings == [
        "[myn] MYN_API_KEY not configured; tools registered but hidden"
    ]


def test_register_survives_bad_config(tmp_path, monkeypatch):
    clear_config(monkeypatch, tmp_path)
    monkeypatch.setenv("MYN_BASE_URL", "http://api.example.com")
    context = Context()

    register(context)

    assert context.tools == []
    assert context.hooks == []
    assert context.commands == []
    assert len(context.logger.warnings) == 1
    assert "Refusing non-HTTPS base_url" in context.logger.warnings[0]
