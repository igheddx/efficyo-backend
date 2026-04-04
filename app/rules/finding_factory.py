"""Finding factory: converts a matched RuleDefinition + ResourceSnapshot into a Finding ORM row."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from app.models.finding import Finding
from app.models.resource_snapshot import ResourceSnapshot
from app.rules.registry import RuleDefinition
from app.services.finding_templates import build_finding_evidence


def build_finding_from_rule(
    rule: RuleDefinition,
    snapshot: ResourceSnapshot,
    detected_at: datetime,
    tenant_id: UUID,
    cloud_account_id: UUID,
    sync_run_id: UUID,
) -> Finding:
    """Create a Finding ORM instance from a matched config-driven rule.

    Evidence fields are taken from ``snapshot.configuration_json`` (for keys
    listed in ``rule.evidence_fields``) and from any ``evidence_computed``
    entries.  The result is wrapped with ``build_finding_evidence`` so the
    payload structure is identical to legacy hardcoded findings.
    """
    cfg: dict[str, Any] = snapshot.configuration_json or {}
    tags: dict[str, Any] = snapshot.tags_json or {}

    # Collect raw evidence fields
    evidence: dict[str, Any] = {}
    for fld in rule.evidence_fields:
        val = cfg.get(fld)
        if val is not None:
            evidence[fld] = val

    # Inject tag evidence for tag-governance rules
    if any(c.op == "any_tag_missing" for c in rule.conditions):
        missing = [t for t in (rule.conditions[0].tags if rule.conditions else []) if not tags.get(t)]
        # Rebuild from all tag conditions
        all_required_tags: list[str] = []
        for c in rule.conditions:
            if c.op == "any_tag_missing":
                all_required_tags.extend(c.tags)
        missing = [t for t in all_required_tags if not tags.get(t)]
        evidence["missing_tags"] = missing
        evidence["tags"] = dict(tags)

    # Computed evidence (e.g., days_remaining from a date field)
    for comp in rule.evidence_computed:
        if comp.op == "days_until":
            raw_date = cfg.get(comp.source_field)
            if raw_date and isinstance(raw_date, str):
                try:
                    dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    evidence[comp.key] = (dt - datetime.now(timezone.utc)).days
                except ValueError:
                    pass

    summary = rule.summary_template.replace("{resource_id}", str(snapshot.resource_id))

    evidence_payload = build_finding_evidence(
        title=rule.title,
        summary=summary,
        category=rule.category,
        risk=rule.severity,
        confidence=rule.confidence,
        recommendation_seed=rule.recommendation_type,
        approval_required=rule.approval_required,
        execution_eligible=rule.execution_eligible,
        evidence=evidence,
    )

    return Finding(
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        resource_snapshot_id=snapshot.id,
        resource_id=snapshot.resource_id,
        resource_type=snapshot.resource_type,
        finding_type=rule.finding_type,
        severity=rule.severity,
        evidence_json=evidence_payload,
        detected_at=detected_at,
        sync_run_id=sync_run_id,
    )
