"""Defer / safe-to-wait Copilot branch: two buckets, deduped, deterministic (no LLM)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.recommendation import Recommendation
from app.schemas.ai_response import (
    DeferredImportantItem,
    LowPriorityItem,
    ResponseMeta,
    ResponseSection,
    ResponseSummary,
    StructuredAIResponse,
)
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
    return "Low impact, low risk, negligible savings — safe to defer"


def _why_deferred(rec: Recommendation) -> str:
    if _is_security_significant(rec):
        return "Security exposure — keep on radar once unblocked"
    s = _savings_float(rec)
    if s is not None and s >= _SAVINGS_LOW_THRESHOLD:
        return f"Meaningful savings (est. {_format_savings(s)}) — revisit when gates clear"
    if _risk_norm(rec) in ("high", "medium"):
        return "Elevated risk — not optional cleanup"
    return "Blocked — worth revisiting when execution is ready"


def _deferred_category(rec: Recommendation) -> str:
    if _is_security_significant(rec):
        return "Security"
    rt = _rtype(rec)
    cat = (rec.recommendation_category or "").strip().lower()
    if cat == "cost" or "rightsiz" in rt or "idle" in rt or "unused" in rt or "saving" in rt:
        return "Cost Optimization"
    if any(s in rt for s in _HYGIENE_TYPE_SUBSTRINGS) or cat == "governance":
        return "Governance / Tagging"
    return "Configuration"


_CATEGORY_SORT_ORDER = {"Security": 0, "Cost Optimization": 1, "Governance / Tagging": 2, "Configuration": 3}


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
    category: str = ""

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
        if not self.category:
            self.category = _deferred_category(rec)


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


def _rec_type_label(agg: _Agg) -> str:
    rt = _rtype(agg.sample_rec) if agg.sample_rec else ""
    if any(s in rt for s in _HYGIENE_TYPE_SUBSTRINGS):
        return "Governance / Tagging"
    if any(s in rt for s in _SECURITY_TYPE_SUBSTRINGS):
        return "Security"
    return agg.category or "Configuration"


_NETWORK_TAG_KEYWORDS = ("subnet", "vpc", "route_table", "internet_gateway", "nat_gateway", "security_group")


def _group_similar(groups: dict[str, _Agg]) -> dict[str, _Agg]:
    """Merge network/tagging items into a combined group for cleaner output."""
    network_tag: list[_Agg] = []
    other: dict[str, _Agg] = {}
    for key, agg in groups.items():
        rt = _rtype(agg.sample_rec) if agg.sample_rec else ""
        if any(k in rt for k in _NETWORK_TAG_KEYWORDS) and any(s in rt for s in _HYGIENE_TYPE_SUBSTRINGS):
            network_tag.append(agg)
        else:
            other[key] = agg
    if len(network_tag) > 1:
        merged = _Agg(title_display="Missing required tags (network resources)")
        for a in network_tag:
            if a.sample_rec:
                merged.add(a.sample_rec, a.elig_ee, a.bucket)
            merged.count += a.count - 1  # add() already added 1
        other["__network_tag_merged__"] = merged
    elif len(network_tag) == 1:
        other[list(network_tag[0].title_display)[:50] or "__net__"] = network_tag[0]
    return other


def format_deferral_markdown(
    low_groups: dict[str, _Agg],
    def_groups: dict[str, _Agg],
) -> str:
    n_low = sum(1 for _ in low_groups)
    n_def = sum(1 for _ in def_groups)

    lines: list[str] = [
        "**Summary**",
        f"- Low Priority Items: {n_low}",
        f"- Deferred but Important: {n_def}",
        "",
    ]

    # ── SECTION 1: LOW PRIORITY ──────────────────────────────────────────────
    lines.append("---")
    lines.append("**LOW PRIORITY (Safe to Defer)**")
    lines.append("")
    if not low_groups:
        lines.append("None")
    else:
        merged_low = _group_similar(low_groups)
        # Group by recommendation type label
        type_buckets: dict[str, list[_Agg]] = {}
        for agg in merged_low.values():
            label = _rec_type_label(agg)
            type_buckets.setdefault(label, []).append(agg)
        for type_label in sorted(type_buckets):
            lines.append(f"*{type_label}*")
            for agg in sorted(type_buckets[type_label], key=lambda a: (-a.count, a.title_display.lower())):
                lines.append(f"**{agg.title_display}**")
                lines.append(f"- Count: {agg.count}")
                lines.append(f"- Reason: {_why_low(agg.sample_rec) if agg.sample_rec else 'Low impact'}")
                lines.append("")

    # ── SECTION 2: DEFERRED BUT IMPORTANT ──────────────────────────────────
    lines.append("---")
    lines.append("**DEFERRED BUT IMPORTANT**")
    lines.append("")
    if not def_groups:
        lines.append("None")
    else:
        merged_def = _group_similar(def_groups)
        # Group by category
        cat_buckets: dict[str, list[_Agg]] = {}
        for agg in merged_def.values():
            cat_buckets.setdefault(agg.category or "Configuration", []).append(agg)

        for cat in sorted(cat_buckets, key=lambda c: _CATEGORY_SORT_ORDER.get(c, 99)):
            lines.append(f"**[Category: {cat}]**")
            lines.append("")
            # Sort within category: elevated risk first, then by savings desc, then count desc
            def _sort_key(a: _Agg) -> tuple:
                risk = _risk_norm(a.sample_rec) if a.sample_rec else "unspecified"
                risk_order = {"high": 0, "medium": 1, "low": 2, "unspecified": 3}[risk]
                sec = 0 if (a.sample_rec and _is_security_significant(a.sample_rec)) else 1
                return (sec, risk_order, -(a.max_savings or 0), -a.count)

            for agg in sorted(cat_buckets[cat], key=_sort_key):
                lines.append(f"**{agg.title_display}**")
                lines.append(f"- Count: {agg.count}")
                lines.append(f"- Why it matters: {_why_deferred(agg.sample_rec) if agg.sample_rec else 'Blocked'}")
                lines.append(f"- Status: {_execution_eligibility_label(agg.elig_ee)}")
                lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ── Structured output ─────────────────────────────────────────────────────────

_CAT_TO_TYPE: dict[str, str] = {
    "Security": "security",
    "Cost Optimization": "cost",
    "Governance / Tagging": "governance",
    "Configuration": "configuration",
}


def _cat_to_type(cat: str) -> str:
    return _CAT_TO_TYPE.get(cat, "configuration")


def format_deferral_structured(
    low_groups: dict[str, _Agg],
    def_groups: dict[str, _Agg],
) -> StructuredAIResponse:
    """Return a :class:`StructuredAIResponse` for the low_priority intent.

    The frontend renders from ``sections[]`` — no markdown parsing required.
    """
    n_low = sum(a.count for a in low_groups.values())
    n_def = sum(a.count for a in def_groups.values())

    sections: list[ResponseSection] = []

    # ── LOW PRIORITY section ──────────────────────────────────────────────────
    if low_groups:
        merged_low = _group_similar(low_groups)
        type_buckets: dict[str, list[_Agg]] = {}
        for agg in merged_low.values():
            label = _rec_type_label(agg)
            type_buckets.setdefault(label, []).append(agg)

        lp_items: list[dict[str, Any]] = []
        for type_label in sorted(type_buckets):
            for agg in sorted(type_buckets[type_label], key=lambda a: (-a.count, a.title_display.lower())):
                lp_items.append(
                    LowPriorityItem(
                        title=agg.title_display,
                        group=type_label,
                        count=agg.count,
                        reason=_why_low(agg.sample_rec) if agg.sample_rec else "Low impact",
                    ).model_dump()
                )
        sections.append(
            ResponseSection(
                section_type="low_priority",
                title="LOW PRIORITY — Safe to Defer",
                items=lp_items,
            )
        )

    # ── DEFERRED BUT IMPORTANT section ───────────────────────────────────────
    if def_groups:
        merged_def = _group_similar(def_groups)
        cat_buckets: dict[str, list[_Agg]] = {}
        for agg in merged_def.values():
            cat_buckets.setdefault(agg.category or "Configuration", []).append(agg)

        def _sort_key_def(a: _Agg) -> tuple:
            risk = _risk_norm(a.sample_rec) if a.sample_rec else "unspecified"
            risk_order = {"high": 0, "medium": 1, "low": 2, "unspecified": 3}[risk]
            sec = 0 if (a.sample_rec and _is_security_significant(a.sample_rec)) else 1
            return (sec, risk_order, -(a.max_savings or 0), -a.count)

        _impact_map: dict[str, str] = {"high": "high", "medium": "medium", "low": "low"}

        def_items: list[dict[str, Any]] = []
        for cat in sorted(cat_buckets, key=lambda c: _CATEGORY_SORT_ORDER.get(c, 99)):
            for agg in sorted(cat_buckets[cat], key=_sort_key_def):
                risk = _risk_norm(agg.sample_rec) if agg.sample_rec else "unspecified"
                def_items.append(
                    DeferredImportantItem(
                        title=agg.title_display,
                        category=_cat_to_type(cat),  # type: ignore[arg-type]
                        group=cat,
                        count=agg.count,
                        status=_execution_eligibility_label(agg.elig_ee),
                        impact=_impact_map.get(risk),  # type: ignore[arg-type]
                        reason=_why_deferred(agg.sample_rec) if agg.sample_rec else "Blocked",
                    ).model_dump()
                )
        sections.append(
            ResponseSection(
                section_type="deferred_important",
                title="DEFERRED BUT IMPORTANT",
                items=def_items,
            )
        )

    return StructuredAIResponse(
        response_type="low_priority",
        title="Low Priority & Deferred Items",
        summary=ResponseSummary(
            counts={"low_priority": n_low, "deferred_important": n_def},
        ),
        sections=sections,
        meta=ResponseMeta(response_type="low_priority"),
    )


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
) -> tuple[str, StructuredAIResponse, int]:
    """Returns (markdown answer, structured response, count of recommendations evaluated)."""
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
    return format_deferral_markdown(low_g, def_g), format_deferral_structured(low_g, def_g), n_eval