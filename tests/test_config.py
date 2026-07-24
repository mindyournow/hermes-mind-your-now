import json
import stat
from pathlib import Path

import pytest

from mind_your_now.config import (
    MynConfigError,
    load_config,
    validate_base_url,
    write_json_0600,
)


def write_config(home: Path, data: dict[str, object]) -> Path:
    path = home / ".hermes" / "mind-your-now.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_env_overrides_json_file(tmp_path, monkeypatch):
    write_config(
        tmp_path,
        {
            "api_key": "file-key",
            "base_url": "https://file.example.com/",
            "agent_name": "File Agent",
            "channel": "file",
        },
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MYN_API_KEY", "env-key")
    monkeypatch.setenv("MYN_BASE_URL", "https://env.example.com/")
    monkeypatch.setenv("MYN_AGENT_NAME", "Env Agent")
    monkeypatch.setenv("MYN_CHANNEL", "env")

    config = load_config()

    assert config.api_key == "env-key"
    assert config.base_url == "https://env.example.com"
    assert config.agent_name == "Env Agent"
    assert config.channel == "env"


def test_rejects_http_base_url(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MYN_BASE_URL", "http://api.example.com")

    with pytest.raises(MynConfigError, match="Refusing non-HTTPS base_url"):
        load_config()


def test_allows_localhost_http(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MYN_BASE_URL", "http://localhost:8080/")

    assert load_config().base_url == "http://localhost:8080"


def test_allows_127_0_0_1_http(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MYN_BASE_URL", "http://127.0.0.1:8080/")

    assert load_config().base_url == "http://127.0.0.1:8080"


def test_write_json_0600_mode(tmp_path):
    path = tmp_path / "nested" / "credentials.json"

    write_json_0600(path, {"api_key": "secret"})

    assert json.loads(path.read_text(encoding="utf-8")) == {"api_key": "secret"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_write_json_0600_tightens_existing_permissions(tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o644)

    write_json_0600(path, {"api_key": "secret"})

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_validate_base_url_strips_trailing_slash():
    assert validate_base_url("https://api.example.com///") == "https://api.example.com"
