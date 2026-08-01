"""Tests for shared tool framework helpers."""

import httpx
import pytest

from mind_your_now.client import MynApiClient
from mind_your_now.tools import fetch_all_unified_tasks, truncate


def test_truncate_slices_list_and_marks_when_cut():
    """truncate slices the list and sets _truncated: true and _totalCount when it cuts."""
    payload = {"items": [1, 2, 3, 4, 5], "other": "data"}
    result = truncate(payload, "items", 3)

    assert result["items"] == [1, 2, 3]
    assert result["_truncated"] is True
    assert result["_totalCount"] == 5
    assert result["other"] == "data"


def test_truncate_no_markers_when_fit():
    """A payload that fits the limit returns unmodified with no markers added."""
    payload = {"items": [1, 2, 3], "other": "data"}
    result = truncate(payload, "items", 5)

    assert result["items"] == [1, 2, 3]
    assert "_truncated" not in result
    assert "_totalCount" not in result
    assert result["other"] == "data"


def test_truncate_exact_fit():
    """When the list exactly fits the limit, no markers are added."""
    payload = {"items": [1, 2, 3]}
    result = truncate(payload, "items", 3)

    assert result["items"] == [1, 2, 3]
    assert "_truncated" not in result
    assert "_totalCount" not in result


def test_truncate_with_offset():
    """truncate respects offset to slice from [offset, offset+limit)."""
    payload = {"items": list(range(10))}
    result = truncate(payload, "items", 3, offset=2)

    assert result["items"] == [2, 3, 4]
    assert result["_truncated"] is True
    assert result["_totalCount"] == 10


def test_truncate_offset_exact_fit():
    """When offset+limit covers all remaining items, markers are still added (it was truncated)."""
    payload = {"items": list(range(5))}
    result = truncate(payload, "items", 3, offset=2)

    assert result["items"] == [2, 3, 4]
    # Even though we got 3 items as requested, the original list was 5 items
    assert result["_truncated"] is True
    assert result["_totalCount"] == 5


def test_truncate_empty_list():
    """truncate handles empty lists correctly."""
    payload = {"items": []}
    result = truncate(payload, "items", 10)

    assert result["items"] == []
    assert "_truncated" not in result


def test_truncate_missing_key():
    """truncate handles missing key gracefully."""
    payload = {"other": "data"}
    result = truncate(payload, "items", 10)

    assert result == payload
    assert "_truncated" not in result


def test_truncate_non_list_value():
    """truncate ignores non-list values."""
    payload = {"items": {"nested": "dict"}}
    result = truncate(payload, "items", 10)

    assert result == payload
    assert "_truncated" not in result


def test_fetch_all_unified_tasks_reads_every_server_page():
    observed = []

    def transport(request):
        params = dict(request.url.params)
        observed.append(params)
        page = int(params["page"])
        start = page * 200
        count = 200 if page == 0 else 5
        return httpx.Response(
            200,
            json=[{"id": f"task-{index}"} for index in range(start, start + count)],
        )

    client = MynApiClient(
        "https://api.example.com",
        "key",
        transport=httpx.MockTransport(transport),
    )

    tasks = fetch_all_unified_tasks(client, params={"type": "HABIT"})

    assert len(tasks) == 205
    assert observed == [
        {"type": "HABIT", "page": "0", "size": "200"},
        {"type": "HABIT", "page": "1", "size": "200"},
    ]


def test_fetch_all_unified_tasks_rejects_non_advancing_pages():
    page = [{"id": f"task-{index}"} for index in range(200)]
    client = MynApiClient(
        "https://api.example.com",
        "key",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=page)),
    )

    with pytest.raises(RuntimeError, match="did not advance"):
        fetch_all_unified_tasks(client)
