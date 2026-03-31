from __future__ import annotations
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.sync_pipeline import SyncTask
from app.services import tenant_service


def aws_account_metadata_collector(db: Session, task: SyncTask) -> dict[str, Any]:
    """
    Collect account-level metadata required by downstream collectors.

    Phase-1 scaffold: we call existing demo-tipwave metadata refresh to keep behavior stable.
    """

    # Keep signature compatible: task.scope_id is cloud_account_id for this collector.
    _cloud_account_id: UUID | None = task.scope_id
    tenant_service.refresh_tipwave_demo_cloud_account_metadata(db)

    return {
        "collector": "account_metadata",
        "cloud_account_id": str(_cloud_account_id) if _cloud_account_id else None,
        "status": "ok",
    }

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.services import tenant_service
from app.models.sync_pipeline import SyncTask


def run_account_metadata_collection(db: Session, task: SyncTask) -> dict[str, Any]:
    """Refresh demo/stable Tipwave cloud-account metadata before other AWS calls."""

    # In the scaffold we only call the existing demo-alignment helper.
    tenant_service.refresh_tipwave_demo_cloud_account_metadata(db)
    return {"collector": "account_metadata", "status": "ok"}


# Registry-compatible handler
def handler(db: Session, task: SyncTask) -> dict[str, Any]:
    return run_account_metadata_collection(db, task)

