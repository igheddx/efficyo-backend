"""Rule definition data model and runtime registry.

``RuleDefinition`` is the canonical in-memory representation of a YAML rule.
``RuleRegistry`` aggregates all loaded rules and exposes lookup helpers used
by the engine, finding factory, and recommendation service.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any


# ---------------------------------------------------------------------------
# Condition primitive
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RuleCondition:
    """A single predicate evaluated against a resource snapshot context.

    Supported operators
    -------------------
    eq / neq                 – field equals / not-equals value (string or numeric)
    gt / gte / lt / lte      – numeric comparison against field
    bool_true / bool_false   – field is truthy / falsy
    exists / missing         – field key is present / absent (or None)
    startswith_any           – string field starts with any prefix in ``values``
    any_bool_true            – any of ``fields`` is truthy
    any_tag_missing          – any tag key in ``tags`` is absent from tags_json
    days_until_lte           – ISO-8601 date field has ≤ ``value`` days remaining
    days_until_gt            – ISO-8601 date field has > ``value`` days remaining
    not_empty_list           – field value (list) is non-empty
    in                       – field value is contained in ``values``
    """

    op: str
    field: str | None = None
    value: Any = None
    values: list[Any] = dc_field(default_factory=list)
    fields: list[str] = dc_field(default_factory=list)
    tags: list[str] = dc_field(default_factory=list)


# ---------------------------------------------------------------------------
# Computed evidence helper (optional, per-rule)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ComputedEvidenceField:
    """Declares a derived value to include in the finding evidence payload.

    Supported ops: ``days_until`` (compute integer days remaining from ISO date field).
    """

    key: str
    op: str
    source_field: str


# ---------------------------------------------------------------------------
# Rule definition
# ---------------------------------------------------------------------------

@dataclass
class RuleDefinition:
    """Fully resolved definition of a single detection rule."""

    rule_id: str
    enabled: bool
    resource_type: str
    finding_type: str
    recommendation_type: str
    category: str                  # security | cost | governance | reliability
    severity: str                  # low | medium | high
    impact: str                    # low | medium | high
    effort: str                    # low | medium | high
    confidence: str                # low | medium | high
    actionability: str             # auto | guided | review_required
    title: str
    summary_template: str          # may contain {resource_id}
    why_this_matters: str
    guided_action_key: str
    recommended_action: str
    approval_required: bool
    execution_eligible: bool
    conditions: list[RuleCondition]
    evidence_fields: list[str]     # cfg keys to include verbatim in evidence
    evidence_computed: list[ComputedEvidenceField] = dc_field(default_factory=list)
    tags: list[str] = dc_field(default_factory=list)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class RuleRegistry:
    """Holds all loaded rule definitions and provides lookup helpers."""

    def __init__(self, rules: list[RuleDefinition] | None = None) -> None:
        self._rules: list[RuleDefinition] = rules or []
        self._by_finding_type: dict[str, RuleDefinition] = {
            r.finding_type: r for r in self._rules if r.enabled
        }
        self._by_resource_type: dict[str, list[RuleDefinition]] = {}
        for r in self._rules:
            if not r.enabled:
                continue
            self._by_resource_type.setdefault(r.resource_type, []).append(r)

    # -- Lookup ----------------------------------------------------------------

    def rules_for_resource_type(self, resource_type: str) -> list[RuleDefinition]:
        """Return all enabled rules targeting ``resource_type``."""
        return self._by_resource_type.get(str(resource_type), [])

    def get_rule_for_finding(self, finding_type: str) -> RuleDefinition | None:
        """Return the enabled rule whose finding_type matches, if any."""
        return self._by_finding_type.get(finding_type)

    def migrated_finding_types(self) -> frozenset[str]:
        """Set of finding_type strings fully managed by config-driven rules."""
        return frozenset(self._by_finding_type.keys())

    def all_rules(self) -> list[RuleDefinition]:
        return list(self._rules)


# ---------------------------------------------------------------------------
# Module-level singleton (populated by loader on first import)
# ---------------------------------------------------------------------------

_registry: RuleRegistry | None = None


def _ensure_loaded() -> None:
    global _registry
    if _registry is None:
        from app.rules.loader import load_all_rules
        _registry = RuleRegistry(load_all_rules())


def get_registry() -> RuleRegistry:
    _ensure_loaded()
    assert _registry is not None
    return _registry


def get_migrated_finding_types() -> frozenset[str]:
    """Convenience wrapper consumed by detection_extended_service."""
    return get_registry().migrated_finding_types()


def get_rule_for_finding(finding_type: str) -> RuleDefinition | None:
    """Used by recommendation_service to look up rule metadata."""
    return get_registry().get_rule_for_finding(finding_type)
