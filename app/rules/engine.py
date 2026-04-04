"""Rule engine: orchestrates config-driven detection for a cloud account.

``run_rule_engine`` is called from the analyzer bundle after (or instead of)
the legacy detection services for migrated finding types.  It:

1. Loads all enabled rules from the registry.
2. Identifies every distinct resource_type targeted by at least one rule.
3. Queries the latest snapshots for each resource_type.
4. Evaluates each rule against each matching snapshot.
5. Emits ``Finding`` rows for rule matches.
6. Returns a ``DetectionRunResult`` compatible with the legacy pipeline.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.db import utc_now
from app.models.finding import Finding
from app.models.resource_snapshot import ResourceSnapshot
from app.services import cloud_account_service
from app.services.detection_service import DetectionRunResult
from app.rules.evaluator import evaluate_conditions
from app.rules.finding_factory import build_finding_from_rule
from app.rules.registry import RuleDefinition, get_registry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Snapshot helper (mirrored from detection_extended_service)
# ---------------------------------------------------------------------------

def _latest_snapshots(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    resource_type: str,
) -> list[ResourceSnapshot]:
    latest_captured = (
        db_session.query(func.max(ResourceSnapshot.captured_at))
        .filter(
            ResourceSnapshot.tenant_id == tenant_id,
            ResourceSnapshot.cloud_account_id == cloud_account_id,
            ResourceSnapshot.resource_type == resource_type,
        )
        .scalar()
    )
    if latest_captured is None:
        return []
    return (
        db_session.query(ResourceSnapshot)
        .filter(
            ResourceSnapshot.tenant_id == tenant_id,
            ResourceSnapshot.cloud_account_id == cloud_account_id,
            ResourceSnapshot.captured_at == latest_captured,
            ResourceSnapshot.resource_type == resource_type,
        )
        .order_by(ResourceSnapshot.resource_id.asc())
        .all()
    )


# ---------------------------------------------------------------------------
# Engine entry point
# ---------------------------------------------------------------------------

def run_rule_engine(
    db_session: Session,
    tenant_id: UUID,
    cloud_account_id: UUID,
    sync_run_id: UUID,
) -> DetectionRunResult:
    """Evaluate all enabled config-driven rules and persist resulting findings."""
    cloud_account_service.get_cloud_account_or_raise(db_session, tenant_id, cloud_account_id)

    registry = get_registry()
    detected_at: datetime = utc_now()
    findings: list[Finding] = []

    # Group rules by resource_type so we load each snapshot set only once
    resource_types: set[str] = {r.resource_type for r in registry.all_rules() if r.enabled}

    for resource_type in sorted(resource_types):
        rules_for_type: list[RuleDefinition] = registry.rules_for_resource_type(resource_type)
        if not rules_for_type:
            continue

        snapshots = _latest_snapshots(db_session, tenant_id, cloud_account_id, resource_type)
        if not snapshots:
            continue

        for snapshot in snapshots:
            cfg = snapshot.configuration_json or {}
            tags = snapshot.tags_json or {}

            for rule in rules_for_type:
                try:
                    if evaluate_conditions(rule.conditions, cfg, tags):
                        finding = build_finding_from_rule(
                            rule=rule,
                            snapshot=snapshot,
                            detected_at=detected_at,
                            tenant_id=tenant_id,
                            cloud_account_id=cloud_account_id,
                            sync_run_id=sync_run_id,
                        )
                        findings.append(finding)
                except Exception:
                    logger.exception(
                        "Rule engine: error evaluating rule %s on resource %s",
                        rule.rule_id,
                        snapshot.resource_id,
                    )

    if findings:
        db_session.add_all(findings)
        db_session.commit()

    logger.info(
        "Rule engine: emitted %d finding(s) for cloud_account=%s",
        len(findings),
        cloud_account_id,
    )

    return DetectionRunResult(
        cloud_account_id=cloud_account_id,
        resource_type="rule_engine",
        findings_created=len(findings),
        detected_at=detected_at,
        sync_run_id=sync_run_id,
    )
