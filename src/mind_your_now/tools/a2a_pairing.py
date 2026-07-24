"""myn_a2a_pairing: secure A2A pairing and message exchange."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from mind_your_now.config import validate_base_url, write_json_0600
from mind_your_now.schemas import action_schema
from mind_your_now.tools import register_myn_tool, tool_error, tool_result


logger = logging.getLogger(__name__)
INVITE_CODE_PATTERN = re.compile(r"^[A-Z]{3}-\d{5}$")
AGENT_NAME_PATTERN = re.compile(r"^(?:[a-z0-9]|[a-z0-9][a-z0-9-]{0,48}[a-z0-9])$")

A2A_SCHEMA = action_schema(
    [
        "redeem_invite",
        "ping",
        "send_message",
        "get_agent_card",
        "status",
        "pair",
        "unpair",
    ],
    {
        "agentKey": {"type": "string"},
        "inviteCode": {"type": "string"},
        "agentName": {"type": "string"},
        "displayName": {"type": "string"},
        "outboundEndpoint": {"type": "string"},
        "capabilities": {"type": "array", "items": {}},
        "intent": {"type": "string", "enum": ["chat", "briefing", "ping"]},
        "message": {"type": "string"},
        "conversationId": {"type": "string"},
    },
)


def _default_credentials_path() -> Path:
    return Path("~/.hermes/mind-your-now/a2a.json").expanduser()


def _load_credentials(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[myn_a2a] Unable to read pairing credentials: %s", exc)
        return None
    return data if isinstance(data, dict) else None


def _capability_manifest(agent_name: str, capabilities: list[Any]) -> dict[str, Any]:
    return {
        "schemaVersion": "1.0",
        "agentInfo": {"name": agent_name, "version": "1.0.0"},
        "capabilities": capabilities,
    }


def _capability_hash(manifest: dict[str, Any]) -> str:
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _request(
    base_url: str,
    method: str,
    path: str,
    *,
    json_body: Any = None,
    headers: dict[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> Any:
    validated = validate_base_url(base_url)
    with httpx.Client(
        base_url=validated,
        timeout=15.0,
        transport=transport,
    ) as client:
        response = client.request(method, path, json=json_body, headers=headers)
    if not response.is_success:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:500]}")
    if response.status_code == 204 or not response.content:
        return None
    return response.json()


def execute_a2a_pairing(
    base_url: str,
    *,
    credentials_path: Path | None = None,
    transport: httpx.BaseTransport | None = None,
    **input_data: Any,
) -> str:
    action = input_data.get("action")
    path = credentials_path or _default_credentials_path()

    if action == "status":
        credentials = _load_credentials(path)
        return tool_result(
            {
                "paired_a2a": credentials is not None,
                "agentId": credentials.get("agentId") if credentials else None,
                "agentName": credentials.get("agentName") if credentials else None,
            }
        )

    if action == "unpair":
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            return tool_error(f"Unable to remove pairing credentials: {exc}")
        return tool_result({"paired_a2a": False, "unpaired": True})

    try:
        validate_base_url(base_url)
    except ValueError as exc:
        return tool_error(
            f"STOP: {exc}. The API URL is pre-configured — contact support if you believe this is an error."
        )

    capabilities = input_data.get("capabilities") or []

    if action in {"pair", "redeem_invite"}:
        invite_code = input_data.get("inviteCode")
        agent_name = input_data.get("agentName") or ("hermes" if action == "pair" else None)
        if not invite_code or not agent_name:
            return tool_error(
                "STOP: inviteCode and agentName are required. Do not retry — ask the user for the missing values."
            )
        if not INVITE_CODE_PATTERN.fullmatch(invite_code):
            return tool_error(
                "STOP: inviteCode must be in the format ABC-12345 (3 uppercase letters, dash, 5 digits). Ask the user for the correct invite code from MYN Settings."
            )
        if not AGENT_NAME_PATTERN.fullmatch(agent_name):
            return tool_error(
                'STOP: agentName must be lowercase alphanumeric with optional hyphens (e.g. "hermes"). Do not retry with a different format.'
            )
        outbound = input_data.get("outboundEndpoint") or "none"
        if outbound != "none":
            try:
                validate_base_url(outbound)
            except ValueError as exc:
                return tool_error(f"STOP: invalid outboundEndpoint: {exc}")
        manifest = _capability_manifest(agent_name, capabilities)
        capability_hash = _capability_hash(manifest)
        body = {
            "inviteCode": invite_code,
            "agentName": agent_name,
            "displayName": input_data.get("displayName") or agent_name,
            "outboundEndpoint": outbound,
            "capabilityHash": capability_hash,
            "capabilityManifest": manifest,
        }
        try:
            data = _request(
                base_url,
                "POST",
                "/api/v1/agent/redeem-invite",
                json_body=body,
                transport=transport,
            )
            if not isinstance(data, dict):
                raise RuntimeError("redeem response was not an object")
            endpoint = data.get("mynEndpoint")
            if endpoint:
                validate_base_url(endpoint)
            credentials = {
                key: data[key]
                for key in ("mynInboundKey", "mynEndpoint", "agentId", "agentName")
                if key in data
            }
            write_json_0600(path, credentials)
            return tool_result(
                {
                    **data,
                    "capabilityHash": capability_hash,
                    "note": "Stored mynInboundKey securely for future A2A calls.",
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[myn_a2a] Pairing failed: %s", exc)
            return tool_error(
                f"STOP: A2A request failed: {exc}. Do NOT retry with different URLs — the API URL is pre-configured. Report this error to the user."
            )

    credentials = _load_credentials(path) or {}
    agent_key = input_data.get("agentKey") or credentials.get("mynInboundKey")

    if action == "get_agent_card":
        try:
            return tool_result(
                _request(
                    base_url,
                    "GET",
                    "/.well-known/agent.json",
                    transport=transport,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[myn_a2a] Agent card request failed: %s", exc)
            return tool_error(f"STOP: A2A request failed: {exc}")

    if action == "ping":
        if not agent_key:
            return tool_error(
                "STOP: agentKey is required for ping. This comes from the redeem_invite response (mynInboundKey)."
            )
        body = {"from": input_data.get("agentName") or "hermes", "intent": "ping"}
    elif action == "send_message":
        if not agent_key:
            return tool_error("STOP: agentKey is required for send_message.")
        if not input_data.get("message"):
            return tool_error("STOP: message is required for send_message.")
        body = {
            "from": input_data.get("agentName") or "hermes",
            "intent": input_data.get("intent") or "chat",
            "message": input_data["message"],
        }
        if input_data.get("conversationId"):
            body["conversationId"] = input_data["conversationId"]
        if capabilities:
            body["capabilityHash"] = _capability_hash(
                _capability_manifest(body["from"], capabilities)
            )
    else:
        return tool_error(f"Unknown action: {action}")

    try:
        return tool_result(
            _request(
                base_url,
                "POST",
                "/a2a/message",
                json_body=body,
                headers={"X-Agent-Key": agent_key},
                transport=transport,
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[myn_a2a] Message request failed: %s", exc)
        return tool_error(
            f"STOP: A2A request failed: {exc}. Do NOT retry with different URLs — the API URL is pre-configured. Report this error to the user."
        )


def register_a2a_pairing_tool(
    ctx: Any,
    base_url: str,
    check_fn: Callable[[], bool],
    *,
    credentials_path: Path | None = None,
    transport: httpx.BaseTransport | None = None,
) -> None:
    register_myn_tool(
        ctx,
        name="myn_a2a_pairing",
        schema=A2A_SCHEMA,
        handler=lambda **kwargs: execute_a2a_pairing(
            base_url,
            credentials_path=credentials_path,
            transport=transport,
            **kwargs,
        ),
        check_fn=check_fn,
        description=(
            "Pair Hermes with MYN/Kaia via A2A. Actions: pair, status, unpair, "
            "redeem_invite, ping, send_message, get_agent_card. The MYN API URL "
            "is pre-configured; never guess or change it after an error."
        ),
        emoji="🔗",
    )
