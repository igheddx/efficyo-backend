from __future__ import annotations

from typing import Any


def build_finding_evidence(
    *,
    title: str,
    summary: str,
    category: str,
    risk: str,
    confidence: str,
    recommendation_seed: str,
    approval_required: bool,
    execution_eligible: bool,
    evidence: dict[str, Any],
    impact_summary: str | None = None,
    savings_estimate: float | None = None,
    risk_explanation: str | None = None,
    linked_resources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Canonical finding payload for resource-agnostic pipeline processing."""
    payload = {
        "title": title,
        "summary": summary,
        "category": category,
        "risk": risk,
        "confidence": confidence,
        "recommendation_seed": recommendation_seed,
        "approval_required": approval_required,
        "execution_eligible": execution_eligible,
        "evidence": evidence,
    }
    if impact_summary:
        payload["impact_summary"] = impact_summary
    if isinstance(savings_estimate, (int, float)):
        payload["savings_estimate"] = float(savings_estimate)
    if risk_explanation:
        payload["risk_explanation"] = risk_explanation
    if linked_resources:
        payload["linked_resources"] = linked_resources
    return payload
