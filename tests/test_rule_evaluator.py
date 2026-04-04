"""Tests for the config-driven rule condition evaluator."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.rules.evaluator import evaluate_conditions
from app.rules.registry import RuleCondition


def cond(**kwargs) -> RuleCondition:
    return RuleCondition(**kwargs)


# ── eq / neq ─────────────────────────────────────────────────────────────────

def test_eq_string():
    assert evaluate_conditions([cond(op="eq", field="status", value="DISABLED")], {"status": "DISABLED"}, {})


def test_eq_string_mismatch():
    assert not evaluate_conditions([cond(op="eq", field="status", value="ENABLED")], {"status": "DISABLED"}, {})


def test_eq_numeric():
    assert evaluate_conditions([cond(op="eq", field="target_count", value=0)], {"target_count": 0}, {})


def test_neq_string():
    assert evaluate_conditions([cond(op="neq", field="policy", value="redirect-to-https")], {"policy": "allow-all"}, {})


# ── bool_true / bool_false ────────────────────────────────────────────────────

def test_bool_false_on_false():
    assert evaluate_conditions([cond(op="bool_false", field="deletion_protection_enabled")],
                               {"deletion_protection_enabled": False}, {})


def test_bool_false_on_true():
    assert not evaluate_conditions([cond(op="bool_false", field="deletion_protection_enabled")],
                                   {"deletion_protection_enabled": True}, {})


def test_bool_true_on_true():
    assert evaluate_conditions([cond(op="bool_true", field="has_igw_default_route")],
                               {"has_igw_default_route": True}, {})


def test_bool_true_on_false():
    assert not evaluate_conditions([cond(op="bool_true", field="has_igw_default_route")],
                                   {"has_igw_default_route": False}, {})


# ── numeric comparisons ───────────────────────────────────────────────────────

def test_gte():
    assert evaluate_conditions([cond(op="gte", field="ingress_rule_count", value=8)],
                               {"ingress_rule_count": 10}, {})


def test_gte_equal():
    assert evaluate_conditions([cond(op="gte", field="ingress_rule_count", value=8)],
                               {"ingress_rule_count": 8}, {})


def test_gte_fail():
    assert not evaluate_conditions([cond(op="gte", field="ingress_rule_count", value=8)],
                                   {"ingress_rule_count": 5}, {})


def test_gt():
    assert evaluate_conditions([cond(op="gt", field="timeout", value=120)],
                               {"timeout": 300}, {})


def test_gt_equal_fails():
    assert not evaluate_conditions([cond(op="gt", field="timeout", value=120)],
                                   {"timeout": 120}, {})


def test_lt():
    assert evaluate_conditions([cond(op="lt", field="count", value=5)], {"count": 3}, {})


def test_lte():
    assert evaluate_conditions([cond(op="lte", field="count", value=5)], {"count": 5}, {})


# ── exists / missing ──────────────────────────────────────────────────────────

def test_exists_present():
    assert evaluate_conditions([cond(op="exists", field="viewer_protocol_policy")],
                               {"viewer_protocol_policy": "allow-all"}, {})


def test_exists_absent():
    assert not evaluate_conditions([cond(op="exists", field="viewer_protocol_policy")], {}, {})


def test_missing_absent():
    assert evaluate_conditions([cond(op="missing", field="viewer_protocol_policy")], {}, {})


def test_missing_present():
    assert not evaluate_conditions([cond(op="missing", field="viewer_protocol_policy")],
                                   {"viewer_protocol_policy": "allow-all"}, {})


# ── startswith_any ────────────────────────────────────────────────────────────

def test_startswith_any_match():
    assert evaluate_conditions(
        [cond(op="startswith_any", field="runtime", values=["python2.", "python3.6", "nodejs10"])],
        {"runtime": "python3.6"},
        {},
    )


def test_startswith_any_no_match():
    assert not evaluate_conditions(
        [cond(op="startswith_any", field="runtime", values=["python2.", "python3.6"])],
        {"runtime": "python3.12"},
        {},
    )


def test_startswith_any_empty_runtime():
    assert not evaluate_conditions(
        [cond(op="startswith_any", field="runtime", values=["python2."])],
        {"runtime": ""},
        {},
    )


# ── any_bool_true ─────────────────────────────────────────────────────────────

def test_any_bool_true_match():
    assert evaluate_conditions(
        [cond(op="any_bool_true", fields=["has_world_open_ssh", "has_world_open_rdp"])],
        {"has_world_open_ssh": True, "has_world_open_rdp": False},
        {},
    )


def test_any_bool_true_no_match():
    assert not evaluate_conditions(
        [cond(op="any_bool_true", fields=["has_world_open_ssh", "has_world_open_rdp"])],
        {"has_world_open_ssh": False, "has_world_open_rdp": False},
        {},
    )


# ── all_bool_false ────────────────────────────────────────────────────────────

def test_all_bool_false_match():
    assert evaluate_conditions(
        [cond(op="all_bool_false", fields=["has_world_open_ssh", "has_world_open_rdp"])],
        {"has_world_open_ssh": False, "has_world_open_rdp": False},
        {},
    )


def test_all_bool_false_fails_if_one_true():
    assert not evaluate_conditions(
        [cond(op="all_bool_false", fields=["has_world_open_ssh", "has_world_open_rdp"])],
        {"has_world_open_ssh": True, "has_world_open_rdp": False},
        {},
    )


# ── any_tag_missing ───────────────────────────────────────────────────────────

def test_any_tag_missing_one_tag_absent():
    assert evaluate_conditions(
        [cond(op="any_tag_missing", tags=["Name", "Environment"])],
        {},
        {"Name": "my-sg"},  # Environment missing
    )


def test_any_tag_missing_both_present():
    assert not evaluate_conditions(
        [cond(op="any_tag_missing", tags=["Name", "Environment"])],
        {},
        {"Name": "my-sg", "Environment": "prod"},
    )


def test_any_tag_missing_empty_tags():
    assert evaluate_conditions(
        [cond(op="any_tag_missing", tags=["Name", "Environment"])],
        {},
        {},
    )


# ── in operator ───────────────────────────────────────────────────────────────

def test_in_operator_match():
    assert evaluate_conditions(
        [cond(op="in", field="status", values=["FAILED", "REVOKED", "VALIDATION_TIMED_OUT"])],
        {"status": "REVOKED"},
        {},
    )


def test_in_operator_no_match():
    assert not evaluate_conditions(
        [cond(op="in", field="status", values=["FAILED", "REVOKED"])],
        {"status": "ISSUED"},
        {},
    )


# ── days_until_lte / days_until_gt ───────────────────────────────────────────

def _iso(days_from_now: int) -> str:
    dt = datetime.now(timezone.utc) + timedelta(days=days_from_now)
    return dt.isoformat()


def test_days_until_lte_expiring_soon():
    cfg = {"not_after": _iso(15)}
    assert evaluate_conditions([cond(op="days_until_lte", field="not_after", value=30)], cfg, {})


def test_days_until_lte_not_expiring_soon():
    cfg = {"not_after": _iso(60)}
    assert not evaluate_conditions([cond(op="days_until_lte", field="not_after", value=30)], cfg, {})


def test_days_until_gt_not_expired():
    cfg = {"not_after": _iso(5)}
    assert evaluate_conditions([cond(op="days_until_gt", field="not_after", value=0)], cfg, {})


def test_days_until_gt_expired():
    cfg = {"not_after": _iso(-2)}  # already expired
    assert not evaluate_conditions([cond(op="days_until_gt", field="not_after", value=0)], cfg, {})


# ── AND logic (implicit) ─────────────────────────────────────────────────────

def test_multiple_conditions_all_must_pass():
    """Route table: has_igw_default_route AND NOT has_nat_default_route."""
    cfg = {"has_igw_default_route": True, "has_nat_default_route": False}
    conditions = [
        cond(op="bool_true", field="has_igw_default_route"),
        cond(op="bool_false", field="has_nat_default_route"),
    ]
    assert evaluate_conditions(conditions, cfg, {})


def test_multiple_conditions_one_fails():
    cfg = {"has_igw_default_route": True, "has_nat_default_route": True}
    conditions = [
        cond(op="bool_true", field="has_igw_default_route"),
        cond(op="bool_false", field="has_nat_default_route"),
    ]
    assert not evaluate_conditions(conditions, cfg, {})


# ── Security group case end-to-end ────────────────────────────────────────────

def test_security_group_overly_permissive_matches():
    """sg_overly_permissive: ingress_rule_count >= 8 AND none of the world-open flags."""
    cfg = {
        "ingress_rule_count": 10,
        "has_world_open_ssh": False,
        "has_world_open_rdp": False,
        "has_world_open_all_ports": False,
    }
    conditions = [
        cond(op="gte", field="ingress_rule_count", value=8),
        cond(op="all_bool_false", fields=["has_world_open_ssh", "has_world_open_rdp", "has_world_open_all_ports"]),
    ]
    assert evaluate_conditions(conditions, cfg, {})


def test_security_group_overly_permissive_skipped_when_world_open():
    """When a world-open flag is set, the sg_overly_permissive rule should NOT fire."""
    cfg = {
        "ingress_rule_count": 10,
        "has_world_open_ssh": True,
        "has_world_open_rdp": False,
        "has_world_open_all_ports": False,
    }
    conditions = [
        cond(op="gte", field="ingress_rule_count", value=8),
        cond(op="all_bool_false", fields=["has_world_open_ssh", "has_world_open_rdp", "has_world_open_all_ports"]),
    ]
    assert not evaluate_conditions(conditions, cfg, {})


# ── Unknown operator returns False safely ─────────────────────────────────────

def test_unknown_operator_returns_false():
    assert not evaluate_conditions([cond(op="totally_unknown_op", field="x", value=1)], {"x": 1}, {})
