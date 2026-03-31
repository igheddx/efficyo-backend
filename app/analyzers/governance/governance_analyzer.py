from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session


def run_governance_analyzer(
    db: Session,
    *,
    tenant_id: UUID,
    cloud_account_id: UUID,
    sync_run_id: UUID,
) -> dict[str, Any]:
    _ = (db, tenant_id, cloud_account_id, sync_run_id)
    return {"analyzer": "governance", "status": "stub"}

