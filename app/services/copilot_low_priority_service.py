"""Defer / safe-to-wait Copilot branch: two buckets, deduped, deterministic (no LLM)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.recommendation import Recommendation
from app.services.execution_eligibility_service import compute_execution_eligibility
from app.services import recommendation_service

logger = logging.getLogger(__name__)

_SAVINGS_LOW_THRESHOLD = 10.0

# Positive signals for “hygiene / tagging / metadata / minor housekeeping” (conservative).
_HYGIENE_TYPES = frozenset(
    {
        "s3_add_required_tags",
    }
)
_HYGIENE_TYPE_SUBSTRINGS = ("required_tag", "tagging", "cost_allocation", "metadata", "housekeeping")
_HYGIENE_SUMMARY_HINTS = (
    "required tag",
    "missing tag",
    "cost allocation",
    "metadata",
    "tagging",
    "naming convention",
    "housekeeping",
    "unused ",
    "idle ",
    "old snapshot",
    "orphan",
)
_SECURITY_TYPE_SUBSTRINGS = (
    "public_access",
    "encryption",
    "iam_",
    "bucket_policy",
    "kms",
    "ssl",
    "tls",
    "insecure",
    "overly_permissive",
    "mfa",
    "credential",
)


def _savings_float(rec: Recommendation) -> float | None:
    if rec.estimated_savings is None:
        return None
    try:
        return float(rec.estimated_savings)
    except (TypeError, ValueError):
        return None


def _risk_norm(rec: Recommendation) -> str:
    r = (rec.risk_level or "").strip().lower()
    if r in ("high", "medium", "low"):
        return r
    return "unspecified"


def _rtype(rec: Recommendation) -> str:
    return (rec.recommendation_type or "").strip().lower()


def _is_security_significant(rec: Recommendation) -> bool:
    if (rec.recommendation_category or "").strip().lower() == "security":
        return True
    rt = _rtype(rec)
    return any(tok in rt for tok in _SECURITY_TYPE_SUBSTRINGS)


def _is_hygiene_or_minor_housekeeping(rec: Recommendation) -> bool:
    if _is_security_significant(rec):
        return False
    rt = _rtype(rec)
    if rt in _HYGIENE_TYPES:
        return True
    if any(s in rt for s in _HYGIENE_TYPE_SUBSTRINGS):
        return True
    sm = (rec.summary or "").lower()
    return any(h in sm for h in _HYGIENE_SUMMARY_HINTS)


def _is_ready(ee: dict[str, Any]) -> bool:
    return bool(ee.get("execution_eligible"))


def _eligibility_restrictiveness(ee: dict[str, Any]) -> tuple[int, str]:
    """Higher first element = more restrictive; used to pick worst gate across duplicates."""
    if _is_ready(ee):
        return (0, "ready")
    br = (ee.get("blocking_reason") or "").strip()
    label = (ee.get("display_label") or ee.get("display_status") or "").strip()
    text = label or br or "not_ready"
    # Rough ordering: unknown block still beats ready
    score = 1 + min(len(text), 200)
    if br in ("approval_required", "preflight_required_not_passed"):
        score += 50
    return (score, text)


def _execution_eligibility_label(ee: dict[str, Any]) -> str:
    if _is_ready(ee):
        return "ready"
    label = (ee.get("display_label") or ee.get("display_status") or "").strip()
    if label:
        return label
    br = (ee.get("blocking_reason") or "").strip()
    if br:
        return br
    return "not_ready"


def classify_deferral_bucket(rec: Recommendation, ee: dict[str, Any]) -> Literal["low", "deferred_important"] | None:
    """
    low: small savings, low/unspecified risk, not security-significant, hygiene/housekeeping profile.
    deferred_important: blocked and (meaningful savings or security or medium+ risk).
    None: omit (actionable important work, or indeterminate / conservative).
    """
    s = _savings_float(rec)
    savings_small = s is None or s <= 0 or s < _SAVINGS_LOW_THRESHOLD
    savings_meaningful = s is not None and s >= _SAVINGS_LOW_THRESHOLD
    ready = _is_ready(ee)
    risk = _risk_norm(rec)
    risk_elevated = risk in ("high", "medium")
    security_sig = _is_security_significant(rec)
    hygiene = _is_hygiene_or_minor_housekeeping(rec)

    meaningful = savings_meaningful or risk_elevated or security_sig

    if ready:
        if savings_meaningful or risk_elevated or security_sig:
            return None
        if savings_small and not risk_elevated and not security_sig and hygiene:
            return "low"
        return None

    if meaningful:
        return "deferred_important"

    if savings_small and not risk_elevated and not security_sig and hygiene:
        return "low"

    if risk == "medium":
        return "deferred_important"

    return None


def _why_low(rec: Recommendation) -> str:
    return (
        "Savings are under $10 (or not estimated); risk is not elevated; the change reads as hygiene, "
        "tagging, metadata, or minor housekeeping rather than urgent security or availability work."
    )


def _why_deferred(rec: Recommendation) -> str:
    parts = ["Not eligible to run yet (gated or blocked)."]
    if _is_security_significant(rec):
        parts.append("Security or risk posture means it should stay on the radar once unblocked.")
    elif (_savings_float(rec) or 0) >= _SAVINGS_LOW_THRESHOLD:
        parts.append("Estimated savings or impact merit follow-up after gates clear.")
    elif _risk_norm(rec) in ("high", "medium"):
        parts.append("Elevated risk means it should not be treated as optional cleanup.")
    else:
        parts.append("Worth revisiting when execution is ready.")
    return " ".join(parts)


def _title_key(rec: Recommendation) -> str:
    raw = (rec.summary or rec.recommendation_type or str(rec.id)).strip()
    return raw[:500] if raw else str(rec.id)


def _title_display(rec: Recommendation) -> str:
    return (rec.summary or rec.recommendation_type or str(rec.id))[:220]


def _eligibility_merge_pick(current: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    if _eligibility_restrictiveness(candidate) > _eligibility_restrictiveness(current):
        return candidate
    return current


def _savings_max(a: float | None, b: float | None) -> float | None:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


@dataclass
class _Agg:
    title_display: str
    count: int = 0
    max_savings: float | None = None
    elig_ee: dict[str, Any] = field(default_factory=dict)
    sample_rec: Recommendation | None = None
    bucket: str = ""

    def add(self, rec: Recommendation, ee: dict[str, Any], bucket: str) -> None:
        self.count += 1
        self.max_savings = _savings_max(self.max_savings, _savings_float(rec))
        if not self.elig_ee:
            self.elig_ee = ee
        else:
            self.elig_ee = _eligibility_merge_pick(self.elig_ee, ee)
        self.sample_rec = rec
        self.bucket = bucket
        if not self.title_display:
            self.title_display = _title_display(rec)


def _aggregate(
    rows: list[tuple[Recommendation, dict[str, Any], Literal["low", "deferred_important"]]],
) -> tuple[dict[str, _Agg], dict[str, _Agg]]:
    low_groups: dict[str, _Agg] = {}
    def_groups: dict[str, _Agg] = {}
    for rec, ee, bucket in rows:
        key = _title_key(rec).casefold()
        target = low_groups if bucket == "low" else def_groups
        if key not in target:
            target[key] = _Agg(title_display=_title_display(rec))
        target[key].add(rec, ee, bucket)
    return low_groups, def_groups


def _format_savings(val: float | None) -> str:
    if val is None:
        return "n/a"
    return f"${val:,.2f}"


def format_deferral_markdown(
    low_groups: dict[str, _Agg],
    def_groups: dict[str, _Agg],
) -> str:
    lines: list[str] = [
        "Low Priority Items (Safe to Defer)",
        "",
    ]
    if not low_groups:
        lines.append("None")
        lines.append("")
    else:
        for agg in sorted(low_groups.values(), key=lambda a: a.title_display.lower()):
            lines.append(f"- {agg.title_display}")
            lines.append(f"  Count: {agg.count}")
            lines.append(f"  Savings: {_format_savings(agg.max_savings)}")
            lines.append(f"  Eligibility: {_execution_eligibility_label(agg.elig_ee)}")
            lines.append(f"  Why it is low priority: {_why_low(agg.sample_rec) if agg.sample_rec else ''}")
            lines.append("")

    lines.append("Deferred But Important")
    lines.append("")
    if not def_groups:
        lines.append("None")
        lines.append("")
    else:
        for agg in sorted(def_groups.values(), key=lambda a: a.title_display.lower()):
            lines.append(f"- {agg.title_display}")
            lines.append(f"  Count: {agg.count}")
            lines.append(f"  Savings: {_format_savings(agg.max_savings)}")
            lines.append(f"  Eligibility: {_execution_eligibility_label(agg.elig_ee)}")
            lines.append(
                f"  Why it is deferred but important: "
                f"{_why_deferred(agg.sample_rec) if agg.sample_rec else ''}"
            )
            lines.append("")

    n_low = len(low_groups)
    n_def = len(def_groups)
    lines.append("Summary")
    lines.append("")
    lines.append(f"- Total unique low priority items: {n_low}")
    lines.append(f"- Total unique deferred but important items: {n_def}")
    lines.append(
        "- Hygiene-style items under $10 with no elevated risk can wait. "
        "Anything blocked with meaningful savings, security exposure, or higher risk should stay on your radar for when execution becomes ready."
    )
    return "\n".join(lines).rstrip() + "\n"


def _eligibility_for_rec(
    db: Session,
    organization_id: UUID,
    tenant_id: UUID,
    cloud_account_id: UUID,
    recommendation_id: UUID,
) -> dict[str, Any]:
    try:
        return compute_execution_eligibility(
            db,
            organization_id=organization_id,
            tenant_id=tenant_id,
            cloud_account_id=cloud_account_id,
            recommendation_id=recommendation_id,
        ) or {}
    except Exception:
        logger.debug("low_priority eligibility lookup failed", exc_info=True)
        return {}


def select_deferral_rows(
    db: Session,
    *,
    organization_id: UUID,
    tenant_id: UUID,
    cloud_account_id: UUID,
    max_evaluate: int = 60,
) -> list[tuple[Recommendation, dict[str, Any], Literal["low", "deferred_important"]]]:
    recs = recommendation_service.list_recommendations(
        db, tenant_id, cloud_account_id, latest_only=True
    )[:max_evaluate]

    out: list[tuple[Recommendation, dict[str, Any], Literal["low", "deferred_important"]]] = []
    for rec in recs:
        ee = _eligibility_for_rec(db, organization_id, tenant_id, cloud_account_id, rec.id)
        b = classify_deferral_bucket(rec, ee)
        if b is None:
            continue
        out.append((rec, ee, b))
    return out


def build_low_priority_copilot_answer(
    db: Session,
    *,
    organization_id: UUID,
    tenant_id: UUID,
    cloud_account_id: UUID,
) -> tuple[str, int]:
    """Returns (markdown answer, count of recommendations evaluated)."""
    recs = recommendation_service.list_recommendations(
        db, tenant_id, cloud_account_id, latest_only=True
    )[:60]
    n_eval = len(recs)
    rows = select_deferral_rows(
        db,
        organization_id=organization_id,
        tenant_id=tenant_id,
        cloud_account_id=cloud_account_id,
        max_evaluate=60,
    )
    low_g, def_g = _aggregate(rows)
    return format_deferral_markdown(low_g, def_g), n_eval