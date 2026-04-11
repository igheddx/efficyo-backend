"""Resource-aware recommendation scoring profiles and prioritization helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.models.recommendation import Recommendation


SCORE_LEVELS = ("low", "medium", "high")
ACTIONABILITY_LEVELS = ("review_required", "guided", "auto")

IMPACT_ORDER = {"low": 1, "medium": 2, "high": 3}
EFFORT_ORDER = {"low": 1, "medium": 2, "high": 3}
CONFIDENCE_ORDER = {"low": 1, "medium": 2, "high": 3}
ACTIONABILITY_ORDER = {"review_required": 1, "guided": 2, "auto": 3}


@dataclass(frozen=True)
class RecommendationScoringProfile:
    impact_score: str
    effort_score: str
    confidence_score: str
    actionability_type: str
    why_this_matters: str
    expected_impact: str
    effort_explanation: str
    confidence_reasoning: str


def _normalize_level(value: str | None, allowed: tuple[str, ...], fallback: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in allowed else fallback


def _estimated_savings_value(value: Decimal | float | int | None) -> float:
    if value is None:
        return 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed > 0 else 0.0


def _format_savings_range(value: Decimal | float | int | None) -> str | None:
    amount = _estimated_savings_value(value)
    if amount <= 0:
        return None
    return f"~${amount:,.2f}/month"


def _profile(
    impact: str,
    effort: str,
    confidence: str,
    actionability: str,
    *,
    why: str,
    expected: str,
    effort_explanation: str,
    confidence_reasoning: str,
) -> RecommendationScoringProfile:
    return RecommendationScoringProfile(
        impact_score=_normalize_level(impact, SCORE_LEVELS, "medium"),
        effort_score=_normalize_level(effort, SCORE_LEVELS, "medium"),
        confidence_score=_normalize_level(confidence, SCORE_LEVELS, "medium"),
        actionability_type=_normalize_level(actionability, ACTIONABILITY_LEVELS, "guided"),
        why_this_matters=why,
        expected_impact=expected,
        effort_explanation=effort_explanation,
        confidence_reasoning=confidence_reasoning,
    )


def recommendation_scoring_profile(
    *,
    recommendation_type: str,
    resource_type: str,
    recommendation_category: str,
    estimated_savings: Decimal | float | int | None,
    risk_level: str | None,
    existing_confidence: str | None = None,
) -> RecommendationScoringProfile:
    rtype = str(recommendation_type or "").strip().lower()
    resource = str(resource_type or "").strip().lower()
    category = str(recommendation_category or "").strip().lower()
    risk = str(risk_level or "").strip().lower()
    savings_text = _format_savings_range(estimated_savings)
    fallback_confidence = _normalize_level(existing_confidence, SCORE_LEVELS, "medium")

    if rtype.endswith("_add_required_tags"):
        return _profile(
            "low",
            "low",
            "high",
            "guided",
            why="Required tags drive ownership, cost allocation, and cleaner automation across the platform.",
            expected="Improves governance coverage and makes future automation safer.",
            effort_explanation="This is a configuration-only change with minimal validation and no downtime.",
            confidence_reasoning="Missing-tag evidence is explicit in the finding payload, so false positives are unlikely.",
        )

    if rtype in {
        "aurora_serverless_cost_review",
        "downsize_instance",
        "switch_instance_class",
        "stop_or_schedule",
        "nat_gateway_cost_review",
        "waf_cost_review",
    }:
        expected = "Can materially reduce recurring spend after validation and controlled rollout."
        if savings_text:
            expected = f"Can materially reduce recurring spend ({savings_text}) after validation and controlled rollout."
        return _profile(
            "high",
            "high",
            "medium",
            "review_required",
            why="This changes cost-driving infrastructure and can affect application behavior if the workload is misread.",
            expected=expected,
            effort_explanation="Requires workload review, testing, and change-window coordination before production rollout.",
            confidence_reasoning="The signal is directionally strong, but utilization and future demand still require human validation.",
        )

    if rtype in {
        "lambda_rightsize_memory",
        "reduce_memory",
        "set_reserved_concurrency",
        "target_group_optimize_deregistration_delay",
        "target_group_enable_stickiness",
        "rds_parameter_group_enable_slow_query_log",
        "rds_parameter_group_disable_general_log",
    }:
        return _profile(
            "medium",
            "low",
            "medium",
            "guided",
            why="This is an optimization change that can improve runtime efficiency or operational visibility without major redesign.",
            expected=(
                f"Improves efficiency or observability and may recover {savings_text}."
                if savings_text
                else "Improves efficiency or observability with limited execution overhead."
            ),
            effort_explanation="This is typically a focused configuration update with light validation and rollback available.",
            confidence_reasoning="Observed usage/configuration data supports the recommendation, but runtime behavior should still be checked after change.",
        )

    if rtype == "s3_enable_public_access_block":
        return _profile(
            "high",
            "low",
            "medium",
            "review_required",
            why="S3 public access controls affect bucket accessibility. Application dependencies must be verified before enabling all block settings.",
            expected="Reduces unintended public exposure risk once safe to apply.",
            effort_explanation="The change is configuration-only but requires review of application access patterns first.",
            confidence_reasoning="The configuration gap is clearly detected, but the safety of applying the fix depends on application context.",
        )

    if rtype in {
        "security_group_restrict_world_open_ports",
        "security_group_restrict_ingress",
        "enable_encryption",
        "cloudfront_enforce_https_redirect",
        "cloudfront_review_insecure_protocol_policy",
        "acm_complete_validation",
        "ses_fix_identity_verification",
    }:
        return _profile(
            "high",
            "low",
            "high",
            "guided",
            why="This directly reduces externally exposed risk and addresses a clearly verifiable security posture issue.",
            expected="Lowers immediate exposure risk while keeping the remediation path operationally straightforward.",
            effort_explanation="The change is usually configuration-only, with limited blast radius and no code release required.",
            confidence_reasoning="The evidence is explicit and configuration-driven, so the recommendation is highly reliable.",
        )

    if rtype in {
        "route_table_review_public_egress",
        "break_internet_exposure_chain",
        "target_group_review_target_health",
        "attach_targets_or_cleanup",
        "investigate_unhealthy_targets",
        "fix_health_check_configuration",
        "load_balancer_review_target_health",
        "load_balancer_cleanup_unused",
        "apigateway_public_exposure_review",
    }:
        return _profile(
            "high",
            "medium",
            "high" if rtype in {"target_group_review_target_health", "attach_targets_or_cleanup", "investigate_unhealthy_targets"} else "medium",
            "review_required" if rtype in {"route_table_review_public_egress", "apigateway_public_exposure_review", "load_balancer_cleanup_unused"} else "guided",
            why="This affects traffic flow or internet exposure, so it matters quickly when availability or attack surface is at stake.",
            expected="Improves service safety and reliability when validated against the intended network path.",
            effort_explanation="Requires dependency review and targeted validation because traffic or connectivity can change.",
            confidence_reasoning="The finding is credible, but linked infrastructure and intended routing still need operator confirmation.",
        )

    if rtype in {
        "s3_enable_versioning",
        "s3_add_lifecycle_policy",
        "add_lifecycle_policy",
        "move_to_ia_or_glacier",
        "cloudfront_review_disabled_distribution",
        "eventbridge_add_targets_or_cleanup",
        "eventbridge_review_disabled_rule",
        "lambda_update_runtime",
        "lambda_review_timeout_configuration",
        "acm_investigate_validation_failure",
        "ses_review_sending_configuration",
    }:
        return _profile(
            "medium" if category != "security" else "high",
            "medium" if rtype in {"lambda_update_runtime", "acm_investigate_validation_failure"} else "low",
            "medium" if rtype in {"eventbridge_add_targets_or_cleanup", "cloudfront_review_disabled_distribution"} else "high",
            "guided" if rtype not in {"lambda_update_runtime", "acm_investigate_validation_failure"} else "review_required",
            why="This improves resilience, posture, or long-term operating cost without being the highest-urgency change on its own.",
            expected=(
                f"Improves resilience or operating efficiency and may recover {savings_text}."
                if savings_text
                else "Improves resilience, recoverability, or long-term operating efficiency."
            ),
            effort_explanation="Execution is usually bounded, but some cases require service-specific validation or maintenance planning.",
            confidence_reasoning="The recommendation is supported by observed configuration state, with moderate uncertainty around workload-specific tradeoffs.",
        )

    if rtype in {"route_table_cleanup_unused", "ec2_review_stopped_instance"}:
        return _profile(
            "low",
            "low",
            "high" if rtype == "route_table_cleanup_unused" else "medium",
            "review_required",
            why="This is mostly cleanup work, but removing the wrong resource can still break hidden dependencies.",
            expected=(
                f"Reduces clutter and may recover {savings_text}."
                if savings_text
                else "Reduces clutter and operational drag with limited upside beyond cleanup."
            ),
            effort_explanation="The implementation itself is simple, but ownership and dependency checks should happen first.",
            confidence_reasoning="The stale-resource signal is usually accurate, though dormant dependencies are still possible.",
        )

    if resource == "security_group" or risk == "high":
        return _profile(
            "high",
            "medium",
            fallback_confidence if fallback_confidence == "high" else "medium",
            "guided",
            why="High-risk findings should be prioritized because delayed remediation can expand exposure.",
            expected="Reduces security or reliability risk with clear operational benefit.",
            effort_explanation="Requires scoped validation, but is usually still tractable without a large delivery project.",
            confidence_reasoning="The signal is strong enough to act on, though surrounding dependencies may need verification.",
        )

    if category == "governance":
        return _profile(
            "low",
            "low",
            "high",
            "guided",
            why="Governance recommendations improve consistency, ownership, and reporting quality.",
            expected="Improves platform clarity and future automation readiness.",
            effort_explanation="Changes are usually lightweight and procedural.",
            confidence_reasoning="Governance drift is directly observable from resource metadata.",
        )

    if category == "cost":
        impact = "high" if _estimated_savings_value(estimated_savings) >= 100 else "medium"
        return _profile(
            impact,
            "medium",
            fallback_confidence,
            "review_required" if impact == "high" else "guided",
            why="This targets recurring spend that may be worth reducing once workload assumptions are checked.",
            expected=(
                f"Improves cost efficiency and may recover {savings_text}."
                if savings_text
                else "Improves cost efficiency when the workload can tolerate the change."
            ),
            effort_explanation="Cost changes often need workload validation even when the implementation is technically small.",
            confidence_reasoning="Usage signals support the opportunity, but savings realization depends on sustained workload behavior.",
        )

    return _profile(
        "medium",
        "medium",
        fallback_confidence,
        "guided",
        why="This recommendation addresses an observable posture gap with practical follow-through value.",
        expected="Improves security, reliability, or cost posture once applied.",
        effort_explanation="Expect moderate implementation effort and targeted validation.",
        confidence_reasoning="The recommendation is evidence-backed, but not every workload tradeoff can be inferred automatically.",
    )


def apply_scoring_profile(rec: Recommendation) -> Recommendation:
    profile = recommendation_scoring_profile(
        recommendation_type=rec.recommendation_type,
        resource_type=rec.resource_type,
        recommendation_category=rec.recommendation_category,
        estimated_savings=rec.estimated_savings,
        risk_level=rec.risk_level,
        existing_confidence=rec.confidence_score,
    )
    rec.impact_score = profile.impact_score
    rec.effort_score = profile.effort_score
    rec.confidence_score = profile.confidence_score
    rec.actionability_type = profile.actionability_type
    if not rec.confidence_reason:
        rec.confidence_reason = profile.confidence_reasoning
    return rec


def resolved_scoring_profile(rec: Recommendation) -> RecommendationScoringProfile:
    profile = recommendation_scoring_profile(
        recommendation_type=rec.recommendation_type,
        resource_type=rec.resource_type,
        recommendation_category=rec.recommendation_category,
        estimated_savings=rec.estimated_savings,
        risk_level=rec.risk_level,
        existing_confidence=rec.confidence_score,
    )
    return RecommendationScoringProfile(
        impact_score=_normalize_level(getattr(rec, "impact_score", None), SCORE_LEVELS, profile.impact_score),
        effort_score=_normalize_level(getattr(rec, "effort_score", None), SCORE_LEVELS, profile.effort_score),
        confidence_score=_normalize_level(getattr(rec, "confidence_score", None), SCORE_LEVELS, profile.confidence_score),
        actionability_type=_normalize_level(
            getattr(rec, "actionability_type", None),
            ACTIONABILITY_LEVELS,
            profile.actionability_type,
        ),
        why_this_matters=profile.why_this_matters,
        expected_impact=profile.expected_impact,
        effort_explanation=profile.effort_explanation,
        confidence_reasoning=profile.confidence_reasoning,
    )


def score_value(level: str, *, dimension: str) -> int:
    normalized = str(level or "").strip().lower()
    if dimension == "impact":
        return IMPACT_ORDER.get(normalized, IMPACT_ORDER["medium"])
    if dimension == "effort":
        return EFFORT_ORDER.get(normalized, EFFORT_ORDER["medium"])
    if dimension == "confidence":
        return CONFIDENCE_ORDER.get(normalized, CONFIDENCE_ORDER["medium"])
    if dimension == "actionability":
        return ACTIONABILITY_ORDER.get(normalized, ACTIONABILITY_ORDER["guided"])
    raise ValueError(f"Unsupported dimension: {dimension}")


def computed_priority_score(rec: Recommendation, max_savings: float = 0.0) -> float:
    profile = resolved_scoring_profile(rec)
    impact_factor = score_value(profile.impact_score, dimension="impact") / 3
    effort_inverse_factor = (4 - score_value(profile.effort_score, dimension="effort")) / 3
    confidence_factor = score_value(profile.confidence_score, dimension="confidence") / 3
    actionability_factor = score_value(profile.actionability_type, dimension="actionability") / 3
    score = (
        (0.55 * impact_factor)
        + (0.2 * effort_inverse_factor)
        + (0.15 * confidence_factor)
        + (0.1 * actionability_factor)
    )
    return round(score, 4)


def recommendation_sort_key(rec: Recommendation, max_savings: float = 0.0) -> tuple[Any, ...]:
    profile = resolved_scoring_profile(rec)
    savings = _estimated_savings_value(rec.estimated_savings)
    created = getattr(rec, "created_at", None)
    created_ts = created.timestamp() if isinstance(created, datetime) else 0.0
    return (
        -score_value(profile.impact_score, dimension="impact"),
        score_value(profile.effort_score, dimension="effort"),
        -score_value(profile.confidence_score, dimension="confidence"),
        -score_value(profile.actionability_type, dimension="actionability"),
        -savings,
        -created_ts,
        str(getattr(rec, "id", "")),
    )


def priority_group_for(rec: Recommendation) -> str:
    profile = resolved_scoring_profile(rec)
    impact = profile.impact_score
    effort = profile.effort_score
    confidence = profile.confidence_score
    actionability = profile.actionability_type
    if actionability == "review_required":
        if impact == "high" and effort == "high":
            return "strategic"
        return "review_required"
    if impact == "high" and effort == "low" and confidence in {"medium", "high"}:
        return "quick_win"
    if impact == "high" and effort in {"medium", "high"}:
        return "strategic"
    if impact == "low" and effort == "low":
        return "optional_cleanup"
    return "standard"


def priority_bucket_for(rec: Recommendation) -> str:
    profile = resolved_scoring_profile(rec)
    score = computed_priority_score(rec)
    if profile.impact_score == "high" and score >= 0.72:
        return "high"
    if profile.impact_score in {"high", "medium"} and score >= 0.48:
        return "medium"
    return "low"


def ranking_reason_for(rec: Recommendation) -> str:
    profile = resolved_scoring_profile(rec)
    phrases: list[str] = []
    if profile.impact_score == "high" and profile.effort_score == "low":
        phrases.append("High impact with low implementation effort")
    elif profile.impact_score == "high" and profile.effort_score == "high":
        phrases.append("High impact but strategic implementation effort")
    elif profile.impact_score == "medium":
        phrases.append("Meaningful impact")
    else:
        phrases.append("Lower-impact cleanup opportunity")

    if profile.actionability_type == "review_required":
        phrases.append("human review required before execution")
    elif profile.actionability_type == "auto":
        phrases.append("ready for safe automation")
    else:
        phrases.append("well suited to guided execution")

    if profile.confidence_score == "high":
        phrases.append("high evidence confidence")
    elif profile.confidence_score == "medium":
        phrases.append("moderate evidence confidence")

    return ", ".join(phrases[:3]).capitalize() + "."