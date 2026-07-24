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
