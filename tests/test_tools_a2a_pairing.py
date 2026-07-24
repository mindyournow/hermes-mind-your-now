import json
import stat
import sys
import types
from pathlib import Path

import httpx
import pytest

from mind_your_now.tools.a2a_pairing import register_a2a_pairing_tool


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


def build_handler(base_url, credentials_path, transport):
    context = Context()
    register_a2a_pairing_tool(
        context,
        base_url,
        lambda: True,
        credentials_path=credentials_path,
        transport=httpx.MockTransport(transport),
    )
    return context.registration["handler"]


def test_pair_redeems_and_stores_0600(tmp_path):
    credentials_path = tmp_path / ".hermes" / "mind-your-now" / "a2a.json"
    observed = {}
    response = {
        "mynInboundKey": "inbound-secret",
        "mynEndpoint": "https://api.mindyournow.com/a2a/message",
        "agentId": "agent-1",
        "agentName": "hermes",
    }

    def transport(request):
        observed["method"] = request.method
        observed["path"] = request.url.path
        observed["body"] = json.loads(request.content)
        return httpx.Response(200, json=response)

    result = json.loads(
        build_handler(
            "https://api.mindyournow.com",
            credentials_path,
            transport,
        )(action="pair", inviteCode="ABC-12345")
    )

    assert observed["method"] == "POST"
    assert observed["path"] == "/api/v1/agent/redeem-invite"
    assert observed["body"]["agentName"] == "hermes"
    assert observed["body"]["inviteCode"] == "ABC-12345"
    assert len(observed["body"]["capabilityHash"]) == 64
    assert json.loads(credentials_path.read_text()) == response
    assert stat.S_IMODE(credentials_path.stat().st_mode) == 0o600
    assert result["success"] is True


def test_rejects_http_endpoint_before_request(tmp_path):
    calls = 0

    def transport(_request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    result = json.loads(
        build_handler(
            "http://api.example.com",
            tmp_path / "a2a.json",
            transport,
        )(action="pair", inviteCode="ABC-12345")
    )

    assert result["success"] is False
    assert "Refusing non-HTTPS base_url" in result["error"]
    assert calls == 0


def test_rejects_insecure_outbound_endpoint_before_request(tmp_path):
    calls = 0

    def transport(_request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    result = json.loads(
        build_handler(
            "https://api.mindyournow.com",
            tmp_path / "a2a.json",
            transport,
        )(
            action="pair",
            inviteCode="ABC-12345",
            outboundEndpoint="http://attacker.example.com/callback",
        )
    )

    assert result["success"] is False
    assert "invalid outboundEndpoint" in result["error"]
    assert calls == 0


def test_status_reports_paired_state_and_unpair_removes_credential(tmp_path):
    credentials_path = tmp_path / "a2a.json"
    credentials_path.write_text(
        json.dumps(
            {
                "mynInboundKey": "secret",
                "agentId": "agent-1",
                "agentName": "hermes",
            }
        )
    )
    handler = build_handler(
        "https://api.mindyournow.com",
        credentials_path,
        lambda _request: pytest.fail("request must not be sent"),
    )

    before = json.loads(handler(action="status"))
    unpaired = json.loads(handler(action="unpair"))
    after = json.loads(handler(action="status"))

    assert before["data"] == {
        "paired_a2a": True,
        "agentId": "agent-1",
        "agentName": "hermes",
    }
    assert unpaired["data"] == {"paired_a2a": False, "unpaired": True}
    assert after["data"] == {
        "paired_a2a": False,
        "agentId": None,
        "agentName": None,
    }
    assert not credentials_path.exists()


@pytest.mark.parametrize(
    ("input_data", "method", "path"),
    [
        ({"action": "get_agent_card"}, "GET", "/.well-known/agent.json"),
        ({"action": "ping", "agentKey": "key"}, "POST", "/a2a/message"),
        (
            {"action": "send_message", "agentKey": "key", "message": "Hello"},
            "POST",
            "/a2a/message",
        ),
    ],
)
def test_original_a2a_actions_preserve_methods_and_paths(
    tmp_path, input_data, method, path
):
    observed = []

    def transport(request):
        observed.append((request.method, request.url.path))
        return httpx.Response(200, json={"ok": True})

    result = json.loads(
        build_handler(
            "https://api.mindyournow.com",
            tmp_path / "a2a.json",
            transport,
        )(**input_data)
    )

    assert observed == [(method, path)]
    assert result == {"success": True, "data": {"ok": True}}
