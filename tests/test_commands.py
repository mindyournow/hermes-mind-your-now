import json
import sys
import types

import httpx
import pytest

from mind_your_now.client import MynApiClient
from mind_your_now.commands import USAGE, handle_myn_command
from mind_your_now.config import MynConfig


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


def config(api_key="myn-key"):
    return MynConfig(
        api_key=api_key,
        base_url="https://api.mindyournow.com",
        agent_name="Hermes/hermes-eltmon",
        channel="hermes",
    )


def client():
    return MynApiClient("https://api.mindyournow.com", "myn-key")


def test_status_shape(tmp_path):
    credentials = tmp_path / "a2a.json"
    credentials.write_text(json.dumps({"mynInboundKey": "secret"}))

    result = json.loads(
        handle_myn_command(
            "status",
            client(),
            config(),
            credentials_path=credentials,
        )
    )

    assert result == {
        "api_key_present": True,
        "base_url": "https://api.mindyournow.com",
        "paired_a2a": True,
    }


def test_pair_happy_path(tmp_path):
    credentials = tmp_path / "a2a.json"
    observed = []

    def transport(request):
        observed.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(
            200,
            json={
                "mynInboundKey": "secret",
                "mynEndpoint": "https://api.mindyournow.com/a2a/message",
                "agentId": "agent-1",
                "agentName": "hermes",
            },
        )

    result = json.loads(
        handle_myn_command(
            "pair abc-12345",
            client(),
            config(),
            credentials_path=credentials,
            transport=httpx.MockTransport(transport),
        )
    )

    assert observed[0][0:2] == ("POST", "/api/v1/agent/redeem-invite")
    assert observed[0][2]["inviteCode"] == "ABC-12345"
    assert result["success"] is True
    assert json.loads(credentials.read_text())["mynInboundKey"] == "secret"


def test_unpair_removes_credentials(tmp_path):
    credentials = tmp_path / "a2a.json"
    credentials.write_text(json.dumps({"mynInboundKey": "secret"}))

    result = json.loads(
        handle_myn_command(
            "unpair",
            client(),
            config(),
            credentials_path=credentials,
        )
    )

    assert result == {
        "success": True,
        "data": {"paired_a2a": False, "unpaired": True},
    }
    assert not credentials.exists()


def test_unknown_subcommand_shows_usage(tmp_path):
    result = handle_myn_command(
        "frobnicate",
        client(),
        config(api_key=None),
        credentials_path=tmp_path / "a2a.json",
    )

    assert result == USAGE
    assert "status" in result
    assert "pair" in result
    assert "unpair" in result
