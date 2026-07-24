"""Synchronous HTTP client for the Mind Your Now API."""

from __future__ import annotations

from typing import Any

import httpx

from mind_your_now.config import validate_base_url


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

    def post(self, path: str, json: Any = None) -> Any:
        return self._request("POST", path, json=json)

    def patch(self, path: str, json: Any = None) -> Any:
        return self._request("PATCH", path, json=json)

    def put(self, path: str, json: Any = None) -> Any:
        return self._request("PUT", path, json=json)

    def delete(self, path: str) -> Any:
        return self._request("DELETE", path)
