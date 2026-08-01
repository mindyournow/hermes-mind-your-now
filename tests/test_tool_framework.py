"""Tests for shared tool framework helpers."""

from mind_your_now.tools import truncate


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
