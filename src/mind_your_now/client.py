"""Synchronous HTTP client for the Mind Your Now API."""

from __future__ import annotations

import json as json_module
import logging
from typing import Any

import httpx

from mind_your_now.config import validate_base_url


logger = logging.getLogger(__name__)


class MynApiError(RuntimeError):
    """An unsuccessful response from the Mind Your Now API."""

    def __init__(self, status: int, snippet: str) -> None:
        self.status = status
        self.snippet = snippet
        super().__init__(f"MYN API {status}: {snippet}")


class MynApiClient:
    """Call MYN endpoints using API-key authentication."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=validate_base_url(base_url),
            headers={"X-API-KEY": api_key or ""},
            timeout=15.0,
            transport=transport,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        response = self._client.request(method, path, params=params, json=json)
        if not response.is_success:
            raise MynApiError(response.status_code, response.text[:500])
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params)

    def post(
        self,
        path: str,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        return self._request("POST", path, params=params, json=json)

    def patch(
        self,
        path: str,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        return self._request("PATCH", path, params=params, json=json)

    def put(
        self,
        path: str,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        return self._request("PUT", path, params=params, json=json)

    def delete(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        return self._request("DELETE", path, params=params)

    def guarded_write(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        get_path: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Execute a write (PATCH/PUT/POST/DELETE) with read-before-write state-hash enforcement.

        MIN-740: Read-before-write state-hash protocol for agent write safety.

        Flow:
        1. GET `get_path` (defaults to `path`) to obtain current `stateHash`
        2. Issue the write (PATCH/PUT/POST/DELETE) with `X-MYN-State-Hash` header
        3. On 409 (stale state): use the `currentStateHash` from the 409 body and retry once
           If the 409 body is unparseable or missing `currentStateHash`, re-read the resource.

        Args:
            method: HTTP method (PATCH, PUT, POST, DELETE)
            path: The path to write to
            json: JSON body for the write (omitted for DELETE)
            get_path: The path to read from for the state hash (defaults to path)
            params: Query parameters (for both read and write)

        Returns:
            The response from the write (or None for 204 / empty)

        Raises:
            MynApiError: If the request fails (including 409 after retry)
        """
        read_path = get_path or path

        # Step 1: Read the current state to get the stateHash
        current = self.get(read_path, params=params)
        state_hash = current.get("stateHash") if isinstance(current, dict) else None

        # Step 2: Attempt the write with the state hash
        try:
            return self._write_with_hash(method, path, json=json, params=params, state_hash=state_hash)
        except MynApiError as exc:
            if exc.status == 409:
                # Step 3: On conflict, extract currentStateHash from 409 body and retry once
                state_hash = self._hash_from_conflict(exc.snippet)
                if state_hash is None:
                    # If we couldn't extract it, re-read the resource
                    fresh = self.get(read_path, params=params)
                    state_hash = fresh.get("stateHash") if isinstance(fresh, dict) else None
                return self._write_with_hash(method, path, json=json, params=params, state_hash=state_hash)
            raise

    def _write_with_hash(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        state_hash: str | None = None,
    ) -> Any:
        """Issue a write request with the X-MYN-State-Hash header."""
        # We need to inject a custom header, so we reconstruct the request
        # and use the underlying httpx client's request method with headers
        headers = {}
        if state_hash:
            headers["X-MYN-State-Hash"] = state_hash

        # Use a context manager to add temporary headers
        original_headers = self._client.headers.copy()
        self._client.headers.update(headers)
        try:
            response = self._client.request(
                method,
                path,
                params=params,
                json=json,
            )
            if not response.is_success:
                raise MynApiError(response.status_code, response.text[:500])
            if response.status_code == 204 or not response.content:
                return None
            try:
                return response.json()
            except ValueError:
                return None
        finally:
            # Restore original headers
            self._client.headers = original_headers

    @staticmethod
    def _hash_from_conflict(error_body: str) -> str | None:
        """Extract currentStateHash from a 409 conflict response body.

        The error body is expected to be JSON like: {"error": "...", "currentStateHash": "..."}
        """
        try:
            parsed = json_module.loads(error_body)
            if isinstance(parsed, dict) and "currentStateHash" in parsed:
                return parsed["currentStateHash"]
        except (json_module.JSONDecodeError, ValueError):
            # Body is not valid JSON or doesn't contain currentStateHash
            pass
        return None
