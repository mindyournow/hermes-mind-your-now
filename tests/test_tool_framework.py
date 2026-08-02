"""Tests for shared tool framework helpers."""

import httpx
import pytest

from mind_your_now.client import MynApiClient
from mind_your_now.tools import UnifiedTaskScan, fetch_all_unified_tasks, truncate


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


def test_fetch_all_unified_tasks_reads_every_stable_server_page():
    observed = []
    snapshot = "stable-generation"

    def transport(request):
        params = dict(request.url.params)
        observed.append(params)
        offset = int(params["offset"])
        count = 200 if offset == 0 else 5
        return httpx.Response(
            200,
            json={
                "tasks": [
                    {"id": f"task-{index}"}
                    for index in range(offset, offset + count)
                ],
                "hasMore": offset == 0,
                "snapshot": snapshot,
            },
        )

    client = MynApiClient(
        "https://api.example.com",
        "key",
        transport=httpx.MockTransport(transport),
    )

    scan = fetch_all_unified_tasks(client, params={"type": "HABIT"})

    assert isinstance(scan, UnifiedTaskScan)
    assert scan.complete is True
    assert len(scan.tasks) == 205
    assert observed == [
        {"type": "HABIT", "limit": "200", "offset": "0"},
        {
            "type": "HABIT",
            "limit": "200",
            "offset": "200",
            "snapshot": snapshot,
        },
    ]


def test_fetch_all_unified_tasks_deduplicates_task_ids():
    page_zero = [{"id": f"task-{index}"} for index in range(200)]
    page_one = [{"id": "task-199"}, {"id": "task-200"}]
    snapshot = "stable-generation"

    def transport(request):
        offset = int(request.url.params["offset"])
        return httpx.Response(
            200,
            json={
                "tasks": page_zero if offset == 0 else page_one,
                "hasMore": offset == 0,
                "snapshot": snapshot,
            },
        )

    client = MynApiClient(
        "https://api.example.com",
        "key",
        transport=httpx.MockTransport(transport),
    )

    scan = fetch_all_unified_tasks(client)

    assert isinstance(scan, UnifiedTaskScan)
    assert len(scan.tasks) == 201
    assert scan.tasks[-1]["id"] == "task-200"
    assert sum(task["id"] == "task-199" for task in scan.tasks) == 1


def test_fetch_all_unified_tasks_stops_after_enough_matches():
    observed_offsets = []

    def transport(request):
        offset = int(request.url.params["offset"])
        observed_offsets.append(offset)
        return httpx.Response(
            200,
            json={
                "tasks": [
                    {"id": f"task-{index}", "priority": "CRITICAL"}
                    for index in range(offset, offset + 200)
                ],
                "hasMore": True,
                "snapshot": "stable-generation",
            },
        )

    client = MynApiClient(
        "https://api.example.com",
        "key",
        transport=httpx.MockTransport(transport),
    )

    scan = fetch_all_unified_tasks(
        client,
        match_fn=lambda task: task["priority"] == "CRITICAL",
        stop_after=20,
    )

    assert isinstance(scan, UnifiedTaskScan)
    assert scan.complete is False
    assert len(scan.tasks) == 200
    assert observed_offsets == [0]


def test_fetch_all_unified_tasks_stops_at_hard_page_cap():
    requests = 0

    def transport(request):
        nonlocal requests
        requests += 1
        offset = int(request.url.params["offset"])
        return httpx.Response(
            200,
            json={
                "tasks": [
                    {"id": f"task-{index}"}
                    for index in range(offset, offset + 200)
                ],
                "hasMore": True,
                "snapshot": "stable-generation",
            },
        )

    client = MynApiClient(
        "https://api.example.com",
        "key",
        transport=httpx.MockTransport(transport),
    )

    scan = fetch_all_unified_tasks(client)

    assert isinstance(scan, UnifiedTaskScan)
    assert scan.complete is False
    assert scan.pages == 50
    assert scan.scanned_items == 10_000
    assert requests == 50


def test_fetch_all_unified_tasks_rejects_changed_snapshot():
    original = [{"id": f"task-{index}"} for index in range(200)]

    def transport(request):
        offset = int(request.url.params["offset"])
        return httpx.Response(
            200,
            json={
                "tasks": original if offset == 0 else [{"id": "task-200"}],
                "hasMore": offset == 0,
                "snapshot": "first" if offset == 0 else "changed",
            },
        )

    client = MynApiClient(
        "https://api.example.com",
        "key",
        transport=httpx.MockTransport(transport),
    )

    with pytest.raises(RuntimeError, match="changed during pagination"):
        fetch_all_unified_tasks(client)


def test_fetch_all_unified_tasks_rejects_non_advancing_pages():
    page = [{"id": f"task-{index}"} for index in range(200)]
    client = MynApiClient(
        "https://api.example.com",
        "key",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "tasks": page,
                    "hasMore": True,
                    "snapshot": "stable-generation",
                },
            )
        ),
    )

    with pytest.raises(RuntimeError, match="did not advance"):
        fetch_all_unified_tasks(client)
