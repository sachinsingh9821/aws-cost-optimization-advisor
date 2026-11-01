"""Unit and boundary tests for evidence, the data-quality gate, and both rules."""

from __future__ import annotations

from datetime import timedelta

import pytest

from src.rules.engine import assess_all, assess_resource
from src.rules.evidence import compute_evidence, on_schedule_mask
from src.rules.quality_gate import check_data_quality
from src.schema import Category, EvidenceQuality, Outcome, Resource, RiskLevel
from tests.conftest import AS_OF, make_raw


def res(**kw) -> Resource:
    return Resource.model_validate(make_raw(**kw))


def outcomes(r: Resource, thresholds, pricing) -> dict[Category, Outcome]:
    return {a.category: a.outcome for a in assess_resource(r, thresholds, pricing)}


def get(r: Resource, cat: Category, thresholds, pricing):
    return next(a for a in assess_resource(r, thresholds, pricing) if a.category == cat)


# --- Evidence ----------------------------------------------------------------


def test_evidence_ignores_missing_points_instead_of_zeroing(thresholds):
    cpu = [20.0] * 720
    cpu[:100] = [None] * 100
    ev = compute_evidence(res(cpu=cpu), thresholds)
    assert ev.mean_cpu_pct == 20.0  # would be 17.2 if None were treated as 0
    assert ev.points_present == 620
    assert ev.completeness == pytest.approx(620 / 720)


def test_evidence_p95_and_max(thresholds):
    cpu = [float(i % 100) for i in range(700)] + [99.0] * 20
    ev = compute_evidence(res(cpu=cpu), thresholds)
    assert ev.max_cpu_pct == 99.0
    assert 90 < ev.p95_cpu_pct <= 99


def test_evidence_without_metrics(thresholds):
    ev = compute_evidence(res(cpu_hourly=None, last_observed_at=None), thresholds)
    assert ev.points_present == 0
    assert ev.mean_cpu_pct is None and ev.p95_cpu_pct is None
    assert ev.staleness_hours is None


def test_schedule_mask_counts_50_hours_per_week(thresholds):
    # 28 days starting at a Monday 00:00 -> exactly 4 weeks
    r = res(days=28, end_offset_hours=24 * 5)  # observed_to = Mon 2026-10-26
    assert (r.observed_from).weekday() == 0
    mask = on_schedule_mask(r, thresholds.scheduled_shutdown)
    assert mask.sum() == 4 * 50


def test_off_hours_mean(thresholds):
    r0 = res()
    mask = on_schedule_mask(r0, thresholds.scheduled_shutdown)
    cpu = [50.0 if on else 2.0 for on in mask]
    ev = compute_evidence(res(cpu=cpu), thresholds)
    assert ev.off_hours_mean_cpu_pct == 2.0


# --- Data-quality gate boundaries ---------------------------------------------


@pytest.mark.parametrize(
    ("days", "passed"),
    [(13, False), (14, True), (15, True)],
)
def test_gate_min_days_boundary(thresholds, days, passed):
    gate = check_data_quality(compute_evidence(res(days=days), thresholds), thresholds)
    assert gate.passed is passed


@pytest.mark.parametrize(
    ("missing", "passed"),
    [(72, True), (73, False)],  # 648/720 = 90.0% passes; 647/720 = 89.86% fails
)
def test_gate_completeness_boundary(thresholds, missing, passed):
    cpu = [5.0] * 720
    for i in range(missing):
        cpu[i] = None
    gate = check_data_quality(compute_evidence(res(cpu=cpu), thresholds), thresholds)
    assert gate.passed is passed


@pytest.mark.parametrize(("hours_old", "passed"), [(48, True), (49, False)])
def test_gate_staleness_boundary(thresholds, hours_old, passed):
    last = (AS_OF - timedelta(hours=hours_old)).isoformat()
    gate = check_data_quality(compute_evidence(res(last_observed_at=last), thresholds), thresholds)
    assert gate.passed is passed


def test_gate_rejects_future_observation(thresholds):
    raw = make_raw(end_offset_hours=-48)  # window ends 48 h AFTER as_of
    gate = check_data_quality(
        compute_evidence(Resource.model_validate(raw), thresholds), thresholds
    )
    assert not gate.passed
    assert "after the analysis time" in gate.reasons[0]


def test_gate_fails_with_no_metrics(thresholds):
    ev = compute_evidence(res(cpu_hourly=None, last_observed_at=None), thresholds)
    gate = check_data_quality(ev, thresholds)
    assert not gate.passed and gate.quality == EvidenceQuality.LOW


@pytest.mark.parametrize(
    ("kw", "quality"),
    [
        ({}, EvidenceQuality.HIGH),
        ({"days": 27}, EvidenceQuality.MEDIUM),
        ({"last_observed_at": (AS_OF - timedelta(hours=25)).isoformat()}, EvidenceQuality.MEDIUM),
        ({"cpu": [None] * 30 + [5.0] * 690}, EvidenceQuality.MEDIUM),  # 95.8% complete
    ],
)
def test_evidence_quality_criteria(thresholds, kw, quality):
    gate = check_data_quality(compute_evidence(res(**kw), thresholds), thresholds)
    assert gate.quality == quality


# --- Rightsizing ---------------------------------------------------------------


def test_rightsizing_recommends_idle_resource(thresholds, pricing):
    a = get(res(), Category.RIGHTSIZING, thresholds, pricing)
    assert a.outcome == Outcome.RECOMMEND
    assert a.proposed_instance_type == "m5.large"
    assert a.risk_level == RiskLevel.LOW
    assert a.savings.monthly_usd == pytest.approx(73.0)
    assert a.evidence.mean_cpu_pct == 5.0


@pytest.mark.parametrize(
    ("value", "flag"),
    [(9.99, True), (10.0, False)],  # strict "<" on the mean threshold
)
def test_rightsizing_mean_threshold_is_strict(thresholds, pricing, value, flag):
    a = get(res(cpu=[value] * 720), Category.RIGHTSIZING, thresholds, pricing)
    assert (a.outcome == Outcome.RECOMMEND) is flag


def test_rightsizing_p95_threshold_blocks_low_mean(thresholds, pricing):
    # mean ~6.3% but 10% of hours at 40% -> p95 = 40% -> must not flag
    cpu = [2.5] * 648 + [40.0] * 72
    a = get(res(cpu=cpu), Category.RIGHTSIZING, thresholds, pricing)
    assert a.evidence.mean_cpu_pct < 10
    assert a.outcome == Outcome.NO_ACTION


def test_rightsizing_smallest_size_no_action(thresholds, pricing):
    a = get(
        res(instance_type="m5.large", unit_price_hourly=0.10),
        Category.RIGHTSIZING,
        thresholds,
        pricing,
    )
    assert a.outcome == Outcome.NO_ACTION
    assert "smallest size" in a.problem


def test_rightsizing_unknown_family_no_action(thresholds, pricing):
    a = get(res(instance_type="t3.xlarge"), Category.RIGHTSIZING, thresholds, pricing)
    assert a.outcome == Outcome.NO_ACTION
    assert "not on the ladder" in a.problem


def test_rightsizing_critical_needs_review(thresholds, pricing):
    a = get(
        res(criticality="critical", environment="prod"), Category.RIGHTSIZING, thresholds, pricing
    )
    assert a.outcome == Outcome.NEEDS_HUMAN_REVIEW
    assert a.human_review_required is True
    assert a.risk_level == RiskLevel.HIGH


def test_rightsizing_critical_healthy_is_no_action(thresholds, pricing):
    a = get(
        res(criticality="critical", cpu=[50.0] * 720), Category.RIGHTSIZING, thresholds, pricing
    )
    assert a.outcome == Outcome.NO_ACTION


def test_rightsizing_prod_is_medium_risk(thresholds, pricing):
    a = get(res(environment="prod"), Category.RIGHTSIZING, thresholds, pricing)
    assert a.outcome == Outcome.RECOMMEND
    assert a.risk_level == RiskLevel.MEDIUM


def test_rightsizing_high_projected_peak_is_medium_risk(thresholds, pricing):
    cpu = [3.0] * 719 + [45.0]  # max 45% -> projected 90% > 80%
    a = get(res(cpu=cpu), Category.RIGHTSIZING, thresholds, pricing)
    assert a.outcome == Outcome.RECOMMEND
    assert a.risk_level == RiskLevel.MEDIUM


def test_rightsizing_insufficient_data(thresholds, pricing):
    a = get(res(days=7), Category.RIGHTSIZING, thresholds, pricing)
    assert a.outcome == Outcome.INSUFFICIENT_DATA
    assert a.savings is None
    assert "window 7.0 days" in a.problem


# --- Scheduled shutdown -------------------------------------------------------


def test_shutdown_recommends_eligible_dev(thresholds, pricing):
    r = res(
        schedule_eligible=True, cpu=[40.0] * 720, instance_type="r5.xlarge", unit_price_hourly=0.26
    )
    a = get(r, Category.SCHEDULED_SHUTDOWN, thresholds, pricing)
    assert a.outcome == Outcome.RECOMMEND
    assert a.savings.monthly_usd == pytest.approx(0.26 * 730 * 118 / 168, abs=1e-3)


def test_shutdown_never_for_prod_even_if_tagged(thresholds, pricing):
    r = res(environment="prod", schedule_eligible=True)
    assert outcomes(r, thresholds, pricing)[Category.SCHEDULED_SHUTDOWN] == Outcome.NO_ACTION


def test_shutdown_never_infers_eligibility(thresholds, pricing):
    r0 = res()
    mask = on_schedule_mask(r0, thresholds.scheduled_shutdown)
    r = res(schedule_eligible=False, cpu=[50.0 if m else 0.5 for m in mask])
    assert outcomes(r, thresholds, pricing)[Category.SCHEDULED_SHUTDOWN] == Outcome.NO_ACTION


def test_shutdown_off_hours_activity_raises_risk(thresholds, pricing):
    r = res(schedule_eligible=True, cpu=[30.0] * 720)
    a = get(r, Category.SCHEDULED_SHUTDOWN, thresholds, pricing)
    assert a.outcome == Outcome.RECOMMEND
    assert a.risk_level == RiskLevel.MEDIUM


def test_shutdown_already_within_schedule_no_action(thresholds, pricing):
    # running only 50/168 of the window -> no off-hours left to save
    r = res(schedule_eligible=True, running_hours=720 * 50 / 168)
    assert outcomes(r, thresholds, pricing)[Category.SCHEDULED_SHUTDOWN] == Outcome.NO_ACTION


def test_shutdown_stale_is_insufficient(thresholds, pricing):
    r = res(schedule_eligible=True, last_observed_at=(AS_OF - timedelta(days=5)).isoformat())
    assert outcomes(r, thresholds, pricing)[Category.SCHEDULED_SHUTDOWN] == (
        Outcome.INSUFFICIENT_DATA
    )


# --- Engine --------------------------------------------------------------------


def test_assess_all_rejects_duplicates(thresholds, pricing):
    r = res()
    with pytest.raises(ValueError, match="duplicate"):
        assess_all([r, r], thresholds, pricing)


def test_assess_all_two_assessments_per_resource(thresholds, pricing):
    out = assess_all([res(resource_id="a"), res(resource_id="b")], thresholds, pricing)
    assert len(out) == 4
    assert len({a.id for a in out}) == 4
