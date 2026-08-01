import json

import httpx
import pytest

from mind_your_now.client import MynApiClient, MynApiError
from mind_your_now.config import MynConfigError


def test_sends_x_api_key_header():
    observed = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["api_key"] = request.headers["X-API-KEY"]
        return httpx.Response(200, json={"ok": True})

    client = MynApiClient(
        "https://api.example.com",
        "myn-secret",
        transport=httpx.MockTransport(handler),
    )

    assert client.get("/resource") == {"ok": True}
    assert observed["api_key"] == "myn-secret"


def test_query_params_encoded_not_concatenated():
    observed = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["path"] = request.url.path
        observed["query"] = request.url.params["query"]
        observed["url"] = str(request.url)
        return httpx.Response(200, json=[])

    client = MynApiClient(
        "https://api.example.com",
        "myn-secret",
        transport=httpx.MockTransport(handler),
    )

    client.get("/search", params={"query": "bread & butter"})

    assert observed["path"] == "/search"
    assert observed["query"] == "bread & butter"
    assert "bread+%26+butter" in observed["url"]


def test_non_2xx_raises_myn_api_error():
    body = "x" * 700
    client = MynApiClient(
        "https://api.example.com",
        "myn-secret",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(422, text=body)
        ),
    )

    with pytest.raises(MynApiError) as raised:
        client.get("/resource")

    assert raised.value.status == 422
    assert raised.value.snippet == "x" * 500
    assert len(raised.value.snippet) == 500


def test_rejects_http_base_url():
    with pytest.raises(MynConfigError, match="Refusing non-HTTPS base_url"):
        MynApiClient("http://api.example.com", "myn-secret")


def test_returns_none_for_no_content():
    client = MynApiClient(
        "https://api.example.com",
        "myn-secret",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(204)
        ),
    )

    assert client.delete("/resource/1") is None


def test_write_methods_send_json_body():
    observed = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append((request.method, request.read()))
        return httpx.Response(200, json={"ok": True})

    client = MynApiClient(
        "https://api.example.com",
        "myn-secret",
        transport=httpx.MockTransport(handler),
    )

    client.post("/resource", {"method": "post"})
    client.patch("/resource/1", {"method": "patch"})
    client.put("/resource/1", {"method": "put"})

    assert observed == [
        ("POST", b'{"method":"post"}'),
        ("PATCH", b'{"method":"patch"}'),
        ("PUT", b'{"method":"put"}'),
    ]


# ---- MIN-740: guarded_write read-before-write state-hash tests ----


class MockTransport(httpx.BaseTransport):
    """A mock transport that records requests and returns configured responses."""

    def __init__(self):
        self.requests = []
        self.responses = []  # Queue of responses to return
        self.response_index = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.response_index < len(self.responses):
            response = self.responses[self.response_index]
            self.response_index += 1
            return response
        # Default 404 if no response configured
        return httpx.Response(404, text="Not found")

    def close(self) -> None:
        pass


def make_response(
    status_code: int = 200,
    json_data: dict | None = None,
    text: str | None = None,
) -> httpx.Response:
    """Helper to create httpx.Response objects."""
    if json_data is not None:
        text = json.dumps(json_data)
    return httpx.Response(status_code, text=text or "", headers={"content-type": "application/json"})


def test_guarded_write_sends_get_before_patch():
    """guarded_write issues a GET on the read path before the write."""
    transport = MockTransport()
    transport.responses = [
        make_response(200, {"id": "task-1", "stateHash": "hash-v1", "title": "Task"}),
        make_response(200, {"id": "task-1", "title": "Updated", "stateHash": "hash-v2"}),
    ]

    client = MynApiClient("https://api.example.com", "key", transport=transport)
    result = client.guarded_write(
        "PATCH",
        "/api/v2/tasks/task-1",
        json={"title": "Updated"},
        get_path="/api/v2/tasks/task-1",
    )

    assert result == {"id": "task-1", "title": "Updated", "stateHash": "hash-v2"}
    assert len(transport.requests) == 2

    # First request should be GET
    get_req = transport.requests[0]
    assert get_req.method == "GET"
    assert "/api/v2/tasks/task-1" in str(get_req.url)
    assert "X-MYN-State-Hash" not in get_req.headers

    # Second request should be PATCH with state hash
    patch_req = transport.requests[1]
    assert patch_req.method == "PATCH"
    assert "/api/v2/tasks/task-1" in str(patch_req.url)
    assert patch_req.headers["X-MYN-State-Hash"] == "hash-v1"


def test_guarded_write_sends_state_hash_header():
    """The write request carries X-MYN-State-Hash equal to the GET response's stateHash."""
    transport = MockTransport()
    transport.responses = [
        make_response(200, {"stateHash": "hash-abc123"}),
        make_response(200, {"success": True}),
    ]

    client = MynApiClient("https://api.example.com", "key", transport=transport)
    client.guarded_write("PATCH", "/api/v2/resource", json={"data": "value"})

    patch_req = transport.requests[1]
    assert patch_req.headers["X-MYN-State-Hash"] == "hash-abc123"


def test_guarded_write_defaults_get_path_to_path():
    """When get_path is omitted, the read uses the same path as the write."""
    transport = MockTransport()
    transport.responses = [
        make_response(200, {"stateHash": "hash-1"}),
        make_response(200, {"updated": True}),
    ]

    client = MynApiClient("https://api.example.com", "key", transport=transport)
    client.guarded_write("PUT", "/api/v2/resource/123", json={"data": "new"})

    # Both requests should use the same path
    assert "/api/v2/resource/123" in str(transport.requests[0].url)
    assert "/api/v2/resource/123" in str(transport.requests[1].url)


def test_guarded_write_omits_state_hash_header_when_read_yields_no_hash():
    """When the GET response has no stateHash, the write is sent without X-MYN-State-Hash."""
    transport = MockTransport()
    transport.responses = [
        make_response(200, {"id": "item"}),  # No stateHash
        make_response(200, {"success": True}),
    ]

    client = MynApiClient("https://api.example.com", "key", transport=transport)
    client.guarded_write("POST", "/api/v2/items", json={"name": "new"})

    patch_req = transport.requests[1]
    assert "X-MYN-State-Hash" not in patch_req.headers


def test_guarded_write_retries_on_409_with_currentStateHash():
    """A 409 whose body carries currentStateHash triggers exactly one retry with the new hash."""
    transport = MockTransport()
    transport.responses = [
        make_response(200, {"stateHash": "hash-v1"}),  # Initial read
        make_response(409, {"error": "State changed", "currentStateHash": "hash-v2"}),  # 409 with new hash
        make_response(200, {"success": True}),  # Retry succeeds
    ]

    client = MynApiClient("https://api.example.com", "key", transport=transport)
    result = client.guarded_write("PATCH", "/api/v2/resource", json={"data": "value"})

    assert result == {"success": True}
    assert len(transport.requests) == 3

    # First PATCH uses hash-v1, gets 409
    patch_1 = transport.requests[1]
    assert patch_1.headers["X-MYN-State-Hash"] == "hash-v1"

    # Retry PATCH uses hash-v2
    patch_2 = transport.requests[2]
    assert patch_2.headers["X-MYN-State-Hash"] == "hash-v2"


def test_guarded_write_rereads_on_409_without_currentStateHash():
    """A 409 with an unparseable body triggers one re-read then one retry."""
    transport = MockTransport()
    transport.responses = [
        make_response(200, {"stateHash": "hash-v1"}),  # Initial read
        make_response(409, text="Internal Server Error"),  # 409 with no JSON
        make_response(200, {"stateHash": "hash-v2"}),  # Re-read
        make_response(200, {"success": True}),  # Retry succeeds
    ]

    client = MynApiClient("https://api.example.com", "key", transport=transport)
    result = client.guarded_write("POST", "/api/v2/resource", json={"data": "value"})

    assert result == {"success": True}
    assert len(transport.requests) == 4

    # GET, PATCH, GET, PATCH
    assert transport.requests[0].method == "GET"
    assert transport.requests[1].method == "POST"
    assert transport.requests[2].method == "GET"
    assert transport.requests[3].method == "POST"

    # Retry uses the re-read hash
    retry_patch = transport.requests[3]
    assert retry_patch.headers["X-MYN-State-Hash"] == "hash-v2"


def test_guarded_write_propagates_non_409_errors():
    """A non-409 error propagates without retrying."""
    transport = MockTransport()
    transport.responses = [
        make_response(200, {"stateHash": "hash-1"}),
        make_response(500, text="Internal error"),
    ]

    client = MynApiClient("https://api.example.com", "key", transport=transport)
    with pytest.raises(MynApiError) as exc_info:
        client.guarded_write("PATCH", "/api/v2/resource", json={"data": "value"})

    assert exc_info.value.status == 500
    # Only one write attempt (no retry)
    assert len(transport.requests) == 2


def test_guarded_write_propagates_409_after_retry():
    """A 409 that still fails after retry propagates the error."""
    transport = MockTransport()
    transport.responses = [
        make_response(200, {"stateHash": "hash-v1"}),
        make_response(409, {"error": "Conflict", "currentStateHash": "hash-v2"}),
        make_response(409, {"error": "Still conflicted", "currentStateHash": "hash-v3"}),
    ]

    client = MynApiClient("https://api.example.com", "key", transport=transport)
    with pytest.raises(MynApiError) as exc_info:
        client.guarded_write("PATCH", "/api/v2/resource", json={"data": "value"})

    assert exc_info.value.status == 409
    # GET, PATCH, PATCH (one retry)
    assert len(transport.requests) == 3


def test_guarded_write_delete_method():
    """guarded_write works with DELETE (no json body)."""
    transport = MockTransport()
    transport.responses = [
        make_response(200, {"stateHash": "hash-1"}),
        make_response(204, text=""),
    ]

    client = MynApiClient("https://api.example.com", "key", transport=transport)
    result = client.guarded_write("DELETE", "/api/v2/resource/123")

    assert result is None
    delete_req = transport.requests[1]
    assert delete_req.method == "DELETE"
    assert delete_req.headers["X-MYN-State-Hash"] == "hash-1"


def test_hash_from_conflict_extracts_currentStateHash():
    """_hash_from_conflict extracts currentStateHash from JSON error body."""
    error_body = '{"error": "Conflict", "currentStateHash": "hash-new"}'
    hash_val = MynApiClient._hash_from_conflict(error_body)
    assert hash_val == "hash-new"


def test_hash_from_conflict_returns_none_on_missing_hash():
    """_hash_from_conflict returns None when currentStateHash is absent."""
    error_body = '{"error": "Conflict"}'
    hash_val = MynApiClient._hash_from_conflict(error_body)
    assert hash_val is None


def test_hash_from_conflict_returns_none_on_invalid_json():
    """_hash_from_conflict returns None when the body is not valid JSON."""
    error_body = "Internal Server Error"
    hash_val = MynApiClient._hash_from_conflict(error_body)
    assert hash_val is None
