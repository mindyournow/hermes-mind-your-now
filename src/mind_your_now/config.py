"""Configuration loading and validation for the Mind Your Now plugin."""

from __future__ import annotations

import json
import os
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "https://api.mindyournow.com"
DEFAULT_AGENT_NAME = "Hermes"
DEFAULT_CHANNEL = "hermes"
CONFIG_PATH = Path("~/.hermes/mind-your-now.json")


class MynConfigError(ValueError):
    """Raised when the plugin configuration is invalid."""


@dataclass(frozen=True)
class MynConfig:
    api_key: str | None
    base_url: str
    agent_name: str
    channel: str


def validate_base_url(url: str) -> str:
    """Require TLS except for local development endpoints."""
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" and host not in ("localhost", "127.0.0.1"):
        raise MynConfigError(
            f"Refusing non-HTTPS base_url {url!r} — the API key would be sent in "
            "plaintext. Use https:// (http:// is allowed only for localhost)."
        )
    return url.rstrip("/")


def load_config() -> MynConfig:
    """Load defaults, then the user JSON file, then environment overrides."""
    values: dict[str, Any] = {
        "api_key": None,
        "base_url": DEFAULT_BASE_URL,
        "agent_name": DEFAULT_AGENT_NAME,
        "channel": DEFAULT_CHANNEL,
    }

    config_path = CONFIG_PATH.expanduser()
    if config_path.exists():
        try:
            file_values = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MynConfigError(f"Unable to read {config_path}: {exc}") from exc
        if not isinstance(file_values, dict):
            raise MynConfigError(f"Expected a JSON object in {config_path}")
        for key in values:
            if key in file_values:
                values[key] = file_values[key]

    env_names = {
        "api_key": "MYN_API_KEY",
        "base_url": "MYN_BASE_URL",
        "agent_name": "MYN_AGENT_NAME",
        "channel": "MYN_CHANNEL",
    }
    for key, env_name in env_names.items():
        if env_name in os.environ:
            values[key] = os.environ[env_name]

    base_url = validate_base_url(str(values["base_url"]))
    api_key = values["api_key"]
    return MynConfig(
        api_key=None if api_key is None else str(api_key),
        base_url=base_url,
        agent_name=str(values["agent_name"]),
        channel=str(values["channel"]),
    )


def write_json_0600(path: str | Path, data: Any) -> None:
    """Write JSON while ensuring the credential file is owner-only."""
    destination = Path(path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            fd = -1
            json.dump(data, file, indent=2)
            file.write("\n")
    finally:
        if fd >= 0:
            os.close(fd)
