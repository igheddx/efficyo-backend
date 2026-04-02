from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.platform_setting import PlatformSetting

ALERT_THRESHOLDS_KEY = "ops_alert_thresholds"

DEFAULT_ALERT_THRESHOLDS: dict[str, int] = {
    "api_health_grace_seconds": 300,
    "worker_stale_seconds": 180,
    "backup_max_age_hours": 26,
    "disk_usage_warn_percent": 80,
    "disk_usage_critical_percent": 90,
    "container_restart_warn_count_24h": 3,
}


def _normalize_thresholds(raw: dict[str, Any] | None) -> dict[str, int]:
    merged = dict(DEFAULT_ALERT_THRESHOLDS)
    for key in merged:
        if raw is not None and key in raw:
            try:
                merged[key] = int(raw[key])
            except (TypeError, ValueError):
                pass
    return merged


def get_alert_thresholds(db: Session) -> tuple[dict[str, int], PlatformSetting | None]:
    row = db.query(PlatformSetting).filter(PlatformSetting.setting_key == ALERT_THRESHOLDS_KEY).one_or_none()
    if row is None:
        return dict(DEFAULT_ALERT_THRESHOLDS), None
    thresholds = _normalize_thresholds(row.value_json if isinstance(row.value_json, dict) else None)
    return thresholds, row


def patch_alert_thresholds(
    db: Session,
    patch: dict[str, Any],
    updated_by_email: str | None,
) -> tuple[dict[str, int], PlatformSetting]:
    row = db.query(PlatformSetting).filter(PlatformSetting.setting_key == ALERT_THRESHOLDS_KEY).one_or_none()
    base = _normalize_thresholds(row.value_json if row is not None and isinstance(row.value_json, dict) else None)

    for key, value in patch.items():
        if value is not None and key in base:
            base[key] = int(value)

    if base["disk_usage_critical_percent"] <= base["disk_usage_warn_percent"]:
        raise ValueError("disk_usage_critical_percent must be greater than disk_usage_warn_percent")

    if row is None:
        row = PlatformSetting(
            setting_key=ALERT_THRESHOLDS_KEY,
            value_json=base,
            updated_by_email=updated_by_email,
        )
        db.add(row)
    else:
        row.value_json = base
        row.updated_by_email = updated_by_email

    db.commit()
    db.refresh(row)
    return base, row