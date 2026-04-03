from datetime import datetime

from pydantic import BaseModel, Field


class RootResourceCoverageRow(BaseModel):
    resource_type: str
    has_snapshot_ingestion: bool
    has_tag_governance_detection: bool
    has_tag_governance_recommendation: bool
    finding_type: str | None = None
    recommendation_type: str | None = None
    recommendation_summary_noun: str | None = None


class RootResourceCoverageSummary(BaseModel):
    generated_at: datetime
    total_supported_snapshot_types: int = Field(..., ge=0)
    total_taggable_types: int = Field(..., ge=0)
    total_tag_governance_mapped_types: int = Field(..., ge=0)
    resources: list[RootResourceCoverageRow]
    ingestion_without_tag_governance: list[str]
    governance_without_ingestion: list[str]
