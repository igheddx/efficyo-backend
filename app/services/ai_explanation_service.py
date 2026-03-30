from __future__ import annotations

from decimal import Decimal
from typing import Any


def _fmt_money(v: float | Decimal | None) -> str | None:
    if v is None:
        return None
    try:
        return f"${float(v):,.2f}/month"
    except (TypeError, ValueError):
        return None


def _cost_hint(evidence_json: dict[str, Any] | None) -> str | None:
    if not evidence_json:
        return None
    raw = evidence_json.get("current_monthly_cost")
    amount = _fmt_money(raw if raw is not None else None)
    if amount:
        category = evidence_json.get("category")
        if category:
            return f"Current {category} cost is about {amount}."
        return f"Current monthly cost is about {amount}."
    return None


def generate_ai_explanation(
    *,
    recommendation_type: str,
    resource_id: str,
    estimated_savings: float | Decimal | None,
    risk_level: str,
    recommended_action: str,
    evidence_json: dict[str, Any] | None = None,
) -> str | None:
    """
    Lightweight AI-style explanation generator (deterministic, no external calls).
    Returns concise 2-4 sentence text; caller should fallback to static explanation if None.
    """
    try:
        savings = _fmt_money(estimated_savings)
        risk = (risk_level or "medium").lower()
        rec_type = (recommendation_type or "").replace("_", " ")
        cost_hint = _cost_hint(evidence_json)

        parts: list[str] = []
        parts.append(f"This recommendation targets {resource_id} ({rec_type}) and is prioritized as {risk} risk.")
        if cost_hint:
            parts.append(cost_hint)
        elif savings:
            parts.append(f"It could save approximately {savings} based on recent observed usage patterns.")
        else:
            parts.append("While direct savings are not always measurable, it reduces operational or risk overhead.")

        parts.append("This matters because the issue is recurring and can compound across environments over time.")
        parts.append(f"Next step: {recommended_action}")
        return " ".join(parts[:4])
    except Exception:
        return None

