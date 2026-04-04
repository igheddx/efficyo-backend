"""YAML rule config loader.

Reads every *.yaml file from app/rules/config/ and parses them into
``RuleDefinition`` instances.  Each YAML file must have a top-level ``rules``
list.  YAML anchors/aliases (merge keys ``<<: *anchor``) are fully supported via
the default PyYAML parser; no external dependencies are needed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from app.rules.registry import (
    ComputedEvidenceField,
    RuleCondition,
    RuleDefinition,
)

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).parent / "config"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def load_all_rules() -> list[RuleDefinition]:
    """Load every YAML rule file from the config directory and return all rules."""
    rules: list[RuleDefinition] = []
    for yaml_path in sorted(_CONFIG_DIR.glob("*.yaml")):
        try:
            file_rules = _load_file(yaml_path)
            rules.extend(file_rules)
            logger.debug("Loaded %d rule(s) from %s", len(file_rules), yaml_path.name)
        except Exception:
            logger.exception("Failed to load rule config: %s", yaml_path)
    return rules


# ---------------------------------------------------------------------------
# Per-file loader
# ---------------------------------------------------------------------------

def _load_file(path: Path) -> list[RuleDefinition]:
    with open(path, "r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    if not isinstance(doc, dict) or "rules" not in doc:
        logger.warning("Rule file %s has no 'rules' list; skipping", path.name)
        return []
    raw_rules: list[dict] = doc["rules"]
    result: list[RuleDefinition] = []
    for raw in raw_rules:
        if not isinstance(raw, dict):
            continue
        try:
            result.append(_parse_rule(raw))
        except Exception as exc:
            logger.warning("Skipping malformed rule in %s: %s — %s", path.name, raw.get("rule_id"), exc)
    return result


# ---------------------------------------------------------------------------
# Rule parser
# ---------------------------------------------------------------------------

def _str(d: dict, key: str, default: str = "") -> str:
    return str(d.get(key) or default)


def _bool(d: dict, key: str, default: bool = False) -> bool:
    v = d.get(key)
    if v is None:
        return default
    return bool(v)


def _list(d: dict, key: str) -> list[Any]:
    v = d.get(key)
    return list(v) if isinstance(v, (list, tuple)) else []


def _parse_condition(raw: dict) -> RuleCondition:
    return RuleCondition(
        op=_str(raw, "op"),
        field=raw.get("field"),
        value=raw.get("value"),
        values=[str(x) for x in _list(raw, "values")],
        fields=[str(x) for x in _list(raw, "fields")],
        tags=[str(x) for x in _list(raw, "tags")],
    )


def _parse_computed_field(raw: dict) -> ComputedEvidenceField:
    return ComputedEvidenceField(
        key=_str(raw, "key"),
        op=_str(raw, "op"),
        source_field=_str(raw, "source_field"),
    )


def _parse_rule(raw: dict) -> RuleDefinition:
    conditions = [_parse_condition(c) for c in _list(raw, "conditions")]
    evidence_computed = [_parse_computed_field(c) for c in _list(raw, "evidence_computed")]
    return RuleDefinition(
        rule_id=_str(raw, "rule_id"),
        enabled=_bool(raw, "enabled", default=True),
        resource_type=_str(raw, "resource_type"),
        finding_type=_str(raw, "finding_type"),
        recommendation_type=_str(raw, "recommendation_type"),
        category=_str(raw, "category", "governance"),
        severity=_str(raw, "severity", "medium"),
        impact=_str(raw, "impact", "medium"),
        effort=_str(raw, "effort", "medium"),
        confidence=_str(raw, "confidence", "medium"),
        actionability=_str(raw, "actionability", "guided"),
        title=_str(raw, "title"),
        summary_template=_str(raw, "summary_template"),
        why_this_matters=_str(raw, "why_this_matters"),
        guided_action_key=_str(raw, "guided_action_key"),
        recommended_action=_str(raw, "recommended_action"),
        approval_required=_bool(raw, "approval_required", default=True),
        execution_eligible=_bool(raw, "execution_eligible", default=False),
        conditions=conditions,
        evidence_fields=[str(x) for x in _list(raw, "evidence_fields")],
        evidence_computed=evidence_computed,
        tags=[str(x) for x in _list(raw, "tags")],
    )
