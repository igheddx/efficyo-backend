"""Unit tests for preflight status aggregation (no AWS calls)."""

from app.services.preflight_dry_run_service import _aggregate_preflight_status


def test_aggregate_preflight_blocked_on_any_fail():
    assert (
        _aggregate_preflight_status(
            [
                {"name": "a", "status": "pass", "message": None},
                {"name": "b", "status": "fail", "message": "x"},
                {"name": "c", "status": "warning", "message": "y"},
            ]
        )
        == "blocked"
    )


def test_aggregate_warning_without_fail():
    assert (
        _aggregate_preflight_status(
            [
                {"name": "a", "status": "pass", "message": None},
                {"name": "b", "status": "warning", "message": "y"},
            ]
        )
        == "warning"
    )


def test_aggregate_ready_all_pass():
    assert _aggregate_preflight_status([{"name": "a", "status": "pass", "message": None}]) == "ready"
