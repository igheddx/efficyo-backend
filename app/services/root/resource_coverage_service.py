from __future__ import annotations

from app.core.db import utc_now
from app.schemas.root.resource_coverage import RootResourceCoverageRow, RootResourceCoverageSummary
from app.services.resource_capability_registry import (
    EXTENDED_TAGGABLE_RESOURCE_TYPES,
    SUPPORTED_SNAPSHOT_RESOURCE_TYPES,
    TAG_GOVERNANCE_RESOURCE_SPECS,
)

_RESOURCE_KEY_TO_SNAPSHOT_TYPE: dict[str, str] = {
    "ec2": "ec2_instance",
}


def _snapshot_type_for_governance_key(resource_key: str) -> str:
    return _RESOURCE_KEY_TO_SNAPSHOT_TYPE.get(resource_key, resource_key)


def build_resource_coverage_summary() -> RootResourceCoverageSummary:
    supported = set(SUPPORTED_SNAPSHOT_RESOURCE_TYPES)
    extended_taggable = set(EXTENDED_TAGGABLE_RESOURCE_TYPES)

    governance_by_snapshot_type: dict[str, tuple[str, str, str]] = {}
    for resource_key, (recommendation_type, noun) in TAG_GOVERNANCE_RESOURCE_SPECS.items():
        snapshot_type = _snapshot_type_for_governance_key(resource_key)
        governance_by_snapshot_type[snapshot_type] = (
            f"{resource_key}_missing_required_tags",
            recommendation_type,
            noun,
        )

    rows: list[RootResourceCoverageRow] = []
    for snapshot_type in sorted(supported):
        finding_type, recommendation_type, noun = governance_by_snapshot_type.get(
            snapshot_type,
            (None, None, None),
        )
        has_governance = finding_type is not None
        rows.append(
            RootResourceCoverageRow(
                resource_type=snapshot_type,
                has_snapshot_ingestion=True,
                has_tag_governance_detection=has_governance,
                has_tag_governance_recommendation=has_governance,
                finding_type=finding_type,
                recommendation_type=recommendation_type,
                recommendation_summary_noun=noun,
            )
        )

    governed_types = set(governance_by_snapshot_type.keys())
    ingestion_without_governance = sorted(supported - governed_types)
    governance_without_ingestion = sorted(governed_types - supported)

    return RootResourceCoverageSummary(
        generated_at=utc_now(),
        total_supported_snapshot_types=len(supported),
        total_taggable_types=len(extended_taggable),
        total_tag_governance_mapped_types=len(governed_types),
        resources=rows,
        ingestion_without_tag_governance=ingestion_without_governance,
        governance_without_ingestion=governance_without_ingestion,
    )
