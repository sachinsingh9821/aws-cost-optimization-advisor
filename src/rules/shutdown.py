"""Scheduled-shutdown rule: stop non-prod, tagged-eligible resources outside work hours.

Decision order (first match wins):
  1. Data-quality gate fails                 -> INSUFFICIENT_DATA
  2. environment == prod                     -> NO_ACTION
  3. schedule_eligible is not true           -> NO_ACTION (eligibility is NEVER inferred)
  4. criticality == critical                 -> NEEDS_HUMAN_REVIEW (never auto-scheduled)
  5. already runs only within the schedule   -> NO_ACTION
  6. otherwise                               -> RECOMMEND
"""

from __future__ import annotations

from src.config import Thresholds
from src.rules.common import SHUTDOWN_ASSUMPTIONS
from src.rules.quality_gate import GateResult
from src.savings.estimate import shutdown_savings
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

RULE_ID = "SD-1"


def assess_shutdown(
    resource: Resource, ev: Evidence, gate: GateResult, thresholds: Thresholds
) -> Assessment:
    cfg = thresholds.scheduled_shutdown
    rule = f"{RULE_ID}: environment != prod AND schedule_eligible == true"
    missing = [] if resource.unit_price_hourly is not None else ["Unit price unknown"]
    base = {
        "id": f"{Category.SCHEDULED_SHUTDOWN}:{resource.resource_id}",
        "resource_id": resource.resource_id,
        "category": Category.SCHEDULED_SHUTDOWN,
        "evidence": ev,
        "rule_triggered": rule,
        "evidence_quality": gate.quality,
        "assumptions": SHUTDOWN_ASSUMPTIONS,
        "proposed_instance_type": None,
    }

    def no_rec(outcome: Outcome, problem: str, next_step: str = "None.") -> Assessment:
        return Assessment(
            **base,
            outcome=outcome,
            problem=problem,
            risk_level=None,
            risks=[],
            savings=None,
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
    if resource.environment == Environment.PROD:
        return no_rec(Outcome.NO_ACTION, "Production resources are never scheduled off.")
    if resource.schedule_eligible is not True:
        return no_rec(
            Outcome.NO_ACTION,
            "Not tagged schedule-eligible (eligibility is never inferred from usage).",
            "If the owner confirms it can stop off-hours, set schedule_eligible=true.",
        )

    savings = shutdown_savings(resource, thresholds)
    if savings.inputs["monthly_off_hours"] == 0:
        return no_rec(Outcome.NO_ACTION, "Already runs only within the schedule window.")

    critical = resource.criticality == Criticality.CRITICAL
    risks: list[str] = ["Anything needing the instance off-hours (jobs, demos) will fail."]
    off = ev.off_hours_mean_cpu_pct
    if critical:
        risk = RiskLevel.HIGH
        risks.append("Resource is marked critical.")
    elif off is not None and off > cfg.off_hours_activity_risk_pct:
        risk = RiskLevel.MEDIUM
        risks.append(
            f"Off-hours mean CPU is {off}% (> {cfg.off_hours_activity_risk_pct:g}%): "
            "something runs outside work hours."
        )
    else:
        risk = RiskLevel.LOW

    return Assessment(
        **base,
        outcome=Outcome.NEEDS_HUMAN_REVIEW if critical else Outcome.RECOMMEND,
        problem=(
            f"{resource.environment} resource tagged schedule-eligible runs 24/7; "
            f"off-hours mean CPU {off}%."
        ),
        risk_level=risk,
        risks=risks,
        savings=savings,
        missing_info=missing,
        next_step=(
            "Critical resource: owner must explicitly approve any schedule."
            if critical
            else "Apply a Mon-Fri 09:00-19:00 UTC start/stop schedule; confirm with owner."
        ),
        human_review_required=critical,
    )
