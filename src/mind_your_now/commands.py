"""Hermes slash command for Mind Your Now status and pairing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from mind_your_now.client import MynApiClient
from mind_your_now.config import MynConfig
from mind_your_now.tools.a2a_pairing import execute_a2a_pairing


USAGE = "Usage: /myn <status|pair INVITE|unpair>"


def _default_credentials_path() -> Path:
    return Path("~/.hermes/mind-your-now/a2a.json").expanduser()


def _is_paired(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        return isinstance(json.loads(path.read_text(encoding="utf-8")), dict)
    except (OSError, json.JSONDecodeError):
        return False


def handle_myn_command(
    raw_args: str,
    client: MynApiClient,
    cfg: MynConfig,
    *,
    credentials_path: Path | None = None,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Handle /myn status, pair, and unpair."""
    del client
    path = credentials_path or _default_credentials_path()
    parts = raw_args.strip().split()
    command = parts[0].lower() if parts else "status"

    if command == "status":
        return json.dumps(
            {
                "api_key_present": bool(cfg.api_key),
                "base_url": cfg.base_url,
                "paired_a2a": _is_paired(path),
            },
            sort_keys=True,
        )

    if command == "pair":
        if len(parts) != 2:
            return USAGE
        return execute_a2a_pairing(
            cfg.base_url,
            credentials_path=path,
            transport=transport,
            action="pair",
            inviteCode=parts[1].upper(),
        )

    if command == "unpair":
        return execute_a2a_pairing(
            cfg.base_url,
            credentials_path=path,
            transport=transport,
            action="unpair",
        )

    return USAGE
