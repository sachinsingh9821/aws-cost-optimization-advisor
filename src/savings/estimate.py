"""Estimated monthly savings. Every result is an ESTIMATE under stated assumptions.

Common term:
  running_fraction       = running_hours / window_hours
  monthly_running_hours  = hours_per_month (730) x running_fraction

Rightsizing:  (current_price - proposed_price) x monthly_running_hours
Shutdown:     current_price x max(0, monthly_running_hours - scheduled_on_hours_per_month)
              scheduled_on_hours_per_month = 730 x on_hours_per_week / 168

Missing inputs never become 0: the estimate carries monthly_usd=None and a reason.
"""

from __future__ import annotations

from collections.abc import Iterable

from src.config import LadderStep, Thresholds
from src.schema import Assessment, Outcome, Resource, SavingsEstimate

CANNOT_ESTIMATE = "Cannot estimate with available data"
HOURS_PER_WEEK = 168


def _monthly_running_hours(resource: Resource, thresholds: Thresholds) -> float:
    return thresholds.analysis.hours_per_month * resource.running_hours / resource.window_hours


def rightsizing_savings(
    resource: Resource, proposed: LadderStep | None, thresholds: Thresholds
) -> SavingsEstimate:
    formula = "(current_price - proposed_price) x 730 h x running_fraction"
    current = resource.unit_price_hourly
    inputs: dict[str, float | str | None] = {
        "current_instance_type": resource.instance_type,
        "current_price_hourly": current,
        "proposed_instance_type": proposed.instance_type if proposed else None,
        "proposed_price_hourly": proposed.hourly_price if proposed else None,
        "running_fraction": resource.running_hours / resource.window_hours,
    }
    if current is None:
        reason = f"{CANNOT_ESTIMATE}: current unit price is unknown"
    elif proposed is None:
        reason = f"{CANNOT_ESTIMATE}: no smaller size on the illustrative ladder"
    elif current <= proposed.hourly_price:
        reason = f"{CANNOT_ESTIMATE}: current price is not above the proposed price"
    else:
        monthly = (current - proposed.hourly_price) * _monthly_running_hours(resource, thresholds)
        return SavingsEstimate(monthly_usd=round(monthly, 4), formula=formula, inputs=inputs)
    return SavingsEstimate(
        monthly_usd=None, formula=formula, inputs=inputs, cannot_estimate_reason=reason
    )


def shutdown_savings(resource: Resource, thresholds: Thresholds) -> SavingsEstimate:
    sched = thresholds.scheduled_shutdown
    hpm = thresholds.analysis.hours_per_month
    scheduled_on = hpm * sched.on_hours_per_week / HOURS_PER_WEEK
    running = _monthly_running_hours(resource, thresholds)
    off_hours = max(0.0, running - scheduled_on)
    formula = "current_price x max(0, 730 h x running_fraction - 730 h x on_hours_per_week / 168)"
    inputs: dict[str, float | str | None] = {
        "current_price_hourly": resource.unit_price_hourly,
        "running_fraction": resource.running_hours / resource.window_hours,
        "on_hours_per_week": float(sched.on_hours_per_week),
        "monthly_off_hours": round(off_hours, 4),
    }
    if resource.unit_price_hourly is None:
        return SavingsEstimate(
            monthly_usd=None,
            formula=formula,
            inputs=inputs,
            cannot_estimate_reason=f"{CANNOT_ESTIMATE}: current unit price is unknown",
        )
    return SavingsEstimate(
        monthly_usd=round(resource.unit_price_hourly * off_hours, 4),
        formula=formula,
        inputs=inputs,
    )


def total_estimated_savings(
    assessments: Iterable[Assessment], outcomes: tuple[Outcome, ...] = (Outcome.RECOMMEND,)
) -> float:
    """Sum estimated savings without double counting.

    A resource can receive both a rightsizing and a shutdown recommendation, but the two
    savings are not additive (after downsizing, the shutdown saves less). We count only
    the LARGER estimate per resource: a conservative lower bound on the combined saving.
    Recommendations without a number contribute nothing (they are reported separately).
    """
    best: dict[str, float] = {}
    for a in assessments:
        if a.outcome not in outcomes or a.savings is None or a.savings.monthly_usd is None:
            continue
        best[a.resource_id] = max(best.get(a.resource_id, 0.0), a.savings.monthly_usd)
    return round(sum(best.values()), 2)
