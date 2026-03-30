"""URL-safe organization slugs (unique in DB)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def slugify_name(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower().strip())
    s = s.strip("-")[:200]
    return s or "org"


def ensure_unique_org_slug(db: "Session", base: str, *, exclude_org_id=None) -> str:
    from app.models.organization import Organization

    candidate = base[:255]
    n = 2
    while True:
        q = db.query(Organization.id).filter(Organization.slug == candidate)
        if exclude_org_id is not None:
            q = q.filter(Organization.id != exclude_org_id)
        if q.first() is None:
            return candidate
        suffix = f"-{n}"
        candidate = (base[: 255 - len(suffix)] + suffix).rstrip("-")
        n += 1
