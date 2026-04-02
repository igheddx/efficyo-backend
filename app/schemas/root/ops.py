from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RootAlertThresholds(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    api_health_grace_seconds: int = Field(300, ge=30, le=3600)
    worker_stale_seconds: int = Field(180, ge=30, le=3600)
    backup_max_age_hours: int = Field(26, ge=1, le=168)
    disk_usage_warn_percent: int = Field(80, ge=50, le=98)
    disk_usage_critical_percent: int = Field(90, ge=60, le=99)
    container_restart_warn_count_24h: int = Field(3, ge=1, le=100)
    updated_at: datetime | None = None
    updated_by_email: str | None = None


class RootAlertThresholdsPatch(BaseModel):
    api_health_grace_seconds: int | None = Field(None, ge=30, le=3600)
    worker_stale_seconds: int | None = Field(None, ge=30, le=3600)
    backup_max_age_hours: int | None = Field(None, ge=1, le=168)
    disk_usage_warn_percent: int | None = Field(None, ge=50, le=98)
    disk_usage_critical_percent: int | None = Field(None, ge=60, le=99)
    container_restart_warn_count_24h: int | None = Field(None, ge=1, le=100)