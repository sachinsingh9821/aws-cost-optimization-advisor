"""Rightsizing rule: propose one size down in the same family.

Decision order (first match wins):
  1. Data-quality gate fails                          -> INSUFFICIENT_DATA
  2. NOT (mean < 10% AND p95 < 30%)                   -> NO_ACTION
  3. No smaller size on the ladder / unknown family   -> NO_ACTION
  4. criticality == critical                          -> NEEDS_HUMAN_REVIEW
  5. [v2, C1] projected peak (2 x max) >= 100%        -> NEEDS_HUMAN_REVIEW
  6. otherwise                                        -> RECOMMEND

Critical resources are checked AFTER the utilization signal: a critical resource with
healthy usage has nothing to review, and flagging it anyway would bury reviewers in noise.
"""

from __future__ import annotations

from src.config import Pricing, Thresholds
from src.rules.common import CPU_ONLY_MISSING, RIGHTSIZING_ASSUMPTIONS
from src.rules.quality_gate import GateResult
from src.savings.estimate import rightsizing_savings
from src.schema import (
    Assessment,
    Category,
    Criticality,
    Environment,
    Evidence,
    Outcome,
    Resource,
    RiskLevel,
)


def assess_rightsizing(
    resource: Resource,
    ev: Evidence,
    gate: GateResult,
    thresholds: Thresholds,
    pricing: Pricing,
) -> Assessment:
    cfg = thresholds.rightsizing
    peak_gate = cfg.review_if_projected_peak_pct
    rule = (
        f"{'RS-1' if peak_gate is None else 'RS-2'}: mean CPU < {cfg.max_mean_cpu_pct:g}% "
        f"AND p95 CPU < {cfg.max_p95_cpu_pct:g}%"
    )
    if peak_gate is not None:
        rule += f"; review if 2 x max CPU >= {peak_gate:g}%"
    missing = [CPU_ONLY_MISSING]
    if resource.unit_price_hourly is None:
        missing.append("Unit price unknown")
    base = {
        "id": f"{Category.RIGHTSIZING}:{resource.resource_id}",
        "resource_id": resource.resource_id,
        "category": Category.RIGHTSIZING,
        "evidence": ev,
        "rule_triggered": rule,
        "evidence_quality": gate.quality,
        "assumptions": RIGHTSIZING_ASSUMPTIONS,
    }

    def no_rec(outcome: Outcome, problem: str, next_step: str) -> Assessment:
        return Assessment(
            **base,
            outcome=outcome,
            problem=problem,
            risk_level=None,
            risks=[],
            savings=None,
            proposed_instance_type=None,
            missing_info=missing,
            next_step=next_step,
            human_review_required=False,
        )

    if not gate.passed:
        return no_rec(
            Outcome.INSUFFICIENT_DATA,
            "Insufficient evidence: " + "; ".join(gate.reasons),
            "Fix metric collection and re-run once the data-quality gate passes.",
        )

    # The gate guarantees present points, so these statistics exist.
    assert ev.mean_cpu_pct is not None and ev.p95_cpu_pct is not None
    assert ev.max_cpu_pct is not None
    if not (ev.mean_cpu_pct < cfg.max_mean_cpu_pct and ev.p95_cpu_pct < cfg.max_p95_cpu_pct):
        return no_rec(
            Outcome.NO_ACTION,
            f"Utilization not low enough (mean {ev.mean_cpu_pct}%, p95 {ev.p95_cpu_pct}%).",
            "None.",
        )

    proposed = pricing.one_size_down(resource.instance_type)
    if proposed is None:
        known = pricing.price_of(resource.instance_type) is not None
        why = "already the smallest size" if known else "instance type not on the ladder"
        return no_rec(
            Outcome.NO_ACTION,
            f"Underutilized, but {why}; one-size-down in the same family is not possible.",
            "Consider a cross-family or burstable option (out of scope for this tool).",
        )

    projected_peak = 2 * ev.max_cpu_pct
    risks = [
        f"Projected CPU after downsizing ~{2 * ev.mean_cpu_pct:.1f}% mean, "
        f"~{2 * ev.p95_cpu_pct:.1f}% p95, ~{projected_peak:.1f}% peak.",
        "Memory-bound workloads may degrade; memory is not measured.",
    ]
    critical = resource.criticality == Criticality.CRITICAL
    if critical:
        risk = RiskLevel.HIGH
        risks.append("Resource is marked critical.")
    elif resource.environment == Environment.PROD or projected_peak > cfg.projected_peak_risk_pct:
        risk = RiskLevel.MEDIUM
        if resource.environment == Environment.PROD:
            risks.append("Production workload.")
        if projected_peak > cfg.projected_peak_risk_pct:
            risks.append(f"Projected peak exceeds {cfg.projected_peak_risk_pct:g}%.")
    else:
        risk = RiskLevel.LOW

    # C1: the smaller size cannot serve the observed peak -> a human must decide.
    peak_review = peak_gate is not None and projected_peak >= peak_gate
    if peak_review and not critical:
        risk = RiskLevel.HIGH
        risks.append(
            f"Projected peak ~{projected_peak:.1f}% >= {peak_gate:g}%: one size down cannot "
            "serve the observed peak."
        )
    review = critical or peak_review

    if critical:
        next_step = (
            f"Owner of this critical resource must approve a move to {proposed.instance_type}."
        )
    elif peak_review:
        next_step = (
            f"Check what drives the {ev.max_cpu_pct}% peak and whether it can run slower "
            f"before moving to {proposed.instance_type}."
        )
    else:
        next_step = f"Resize to {proposed.instance_type} in a maintenance window; monitor 7 days."

    return Assessment(
        **base,
        outcome=Outcome.NEEDS_HUMAN_REVIEW if review else Outcome.RECOMMEND,
        problem=(
            f"{resource.instance_type} averages {ev.mean_cpu_pct}% CPU "
            f"(p95 {ev.p95_cpu_pct}%) over {ev.coverage_days:g} days."
        ),
        risk_level=risk,
        risks=risks,
        savings=rightsizing_savings(resource, proposed, thresholds),
        proposed_instance_type=proposed.instance_type,
        missing_info=missing,
        next_step=next_step,
        human_review_required=review,
    )
