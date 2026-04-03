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
) -> dict[str, Any]:
    """Canonical finding payload for resource-agnostic pipeline processing."""
    return {
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
