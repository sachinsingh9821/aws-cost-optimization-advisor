"""Cost engine, savings estimation, and ranking."""

from __future__ import annotations

import math

import pytest

from src.config import LadderStep
from src.cost.engine import cost_breakdown, cost_summary, resource_costs
from src.ranking.prioritize import rank_recommendations
from src.rules.engine import assess_all
from src.savings.estimate import (
    CANNOT_ESTIMATE,
    rightsizing_savings,
    shutdown_savings,
    total_estimated_savings,
)
from src.schema import Outcome, Resource
from tests.conftest import make_raw


def res(**kw) -> Resource:
    return Resource.model_validate(make_raw(**kw))


# --- Cost engine ---------------------------------------------------------------


def test_resource_cost_formulas(thresholds):
    df = resource_costs([res(unit_price_hourly=0.20)], thresholds)
    row = df.iloc[0]
    assert row["window_cost_usd"] == pytest.approx(0.20 * 720)
    assert row["monthly_cost_usd"] == pytest.approx(0.20 * 730)


def test_partial_running_hours_scale_monthly_cost(thresholds):
    df = resource_costs([res(running_hours=360)], thresholds)
    assert df.iloc[0]["monthly_cost_usd"] == pytest.approx(0.20 * 730 * 0.5)


def test_missing_price_is_nan_not_zero_and_counted(thresholds):
    df = resource_costs(
        [res(resource_id="a"), res(resource_id="b", unit_price_hourly=None)], thresholds
    )
    assert math.isnan(df.loc[df.resource_id == "b", "monthly_cost_usd"].iloc[0])
    s = cost_summary(df)
    assert s.priced_count == 1
    assert s.unpriced_ids == ["b"]
    assert s.total_monthly_usd == pytest.approx(146.0)


def test_breakdown_all_unpriced_group_is_nan(thresholds):
    df = resource_costs(
        [
            res(resource_id="a", region="us-east-1"),
            res(resource_id="b", region="eu-west-1", unit_price_hourly=None),
        ],
        thresholds,
    )
    out = cost_breakdown(df, "region").set_index("region")
    assert out.loc["us-east-1", "monthly_cost_usd"] == pytest.approx(146.0)
    assert math.isnan(out.loc["eu-west-1", "monthly_cost_usd"])  # not 0
    assert out.loc["eu-west-1", "unpriced_resources"] == 1


def test_breakdown_unknown_column(thresholds):
    df = resource_costs([res()], thresholds)
    with pytest.raises(ValueError, match="unknown breakdown"):
        cost_breakdown(df, "colour")


def test_cost_rejects_duplicates(thresholds):
    with pytest.raises(ValueError, match="duplicate"):
        resource_costs([res(), res()], thresholds)


def test_empty_input(thresholds):
    df = resource_costs([], thresholds)
    s = cost_summary(df)
    assert s.resource_count == 0 and s.total_monthly_usd == 0.0


# --- Savings -------------------------------------------------------------------

STEP = LadderStep(size="large", instance_type="m5.large", hourly_price=0.10)


def test_rightsizing_savings_formula(thresholds):
    s = rightsizing_savings(res(), STEP, thresholds)
    assert s.kind == "estimated"
    assert s.monthly_usd == pytest.approx(73.0)


def test_rightsizing_savings_scales_with_running_fraction(thresholds):
    s = rightsizing_savings(res(running_hours=180), STEP, thresholds)
    assert s.monthly_usd == pytest.approx(73.0 / 4)


@pytest.mark.parametrize(
    ("kw", "step", "fragment"),
    [
        ({"unit_price_hourly": None}, STEP, "price is unknown"),
        ({}, None, "no smaller size"),
        ({"unit_price_hourly": 0.05}, STEP, "not above the proposed"),
    ],
)
def test_rightsizing_cannot_estimate(thresholds, kw, step, fragment):
    s = rightsizing_savings(res(**kw), step, thresholds)
    assert s.monthly_usd is None
    assert s.cannot_estimate_reason.startswith(CANNOT_ESTIMATE)
    assert fragment in s.cannot_estimate_reason


def test_shutdown_savings_formula(thresholds):
    s = shutdown_savings(res(unit_price_hourly=0.10), thresholds)
    assert s.monthly_usd == pytest.approx(0.10 * 730 * 118 / 168, abs=1e-4)


def test_shutdown_savings_never_negative(thresholds):
    s = shutdown_savings(res(running_hours=100), thresholds)
    assert s.monthly_usd == 0.0


def test_shutdown_savings_unknown_price(thresholds):
    s = shutdown_savings(res(unit_price_hourly=None), thresholds)
    assert s.monthly_usd is None and CANNOT_ESTIMATE in s.cannot_estimate_reason


def test_total_savings_no_double_counting(thresholds, pricing):
    # Idle AND schedule-eligible: two recommendations, count only the larger one.
    r = res(schedule_eligible=True)
    a = assess_all([r], thresholds, pricing)
    recs = [x for x in a if x.outcome == Outcome.RECOMMEND]
    assert len(recs) == 2
    assert total_estimated_savings(a) == pytest.approx(
        max(x.savings.monthly_usd for x in recs), abs=0.01
    )


def test_total_savings_excludes_review_and_unpriced(thresholds, pricing):
    rs = [
        res(resource_id="crit", criticality="critical"),
        res(resource_id="nop", unit_price_hourly=None),
    ]
    a = assess_all(rs, thresholds, pricing)
    assert total_estimated_savings(a) == 0.0
    assert total_estimated_savings(a, (Outcome.NEEDS_HUMAN_REVIEW,)) == pytest.approx(73.0)


# --- Ranking -------------------------------------------------------------------


def test_ranking_order_quality_then_risk_then_savings(thresholds, pricing):
    rs = [
        res(resource_id="medium_q", days=20, instance_type="m5.4xlarge", unit_price_hourly=0.80),
        res(
            resource_id="prod_risk",
            environment="prod",
            instance_type="m5.4xlarge",
            unit_price_hourly=0.80,
        ),
        res(resource_id="small", instance_type="m5.xlarge"),
        res(resource_id="big", instance_type="m5.2xlarge", unit_price_hourly=0.40),
        res(resource_id="noprice", instance_type="m5.2xlarge", unit_price_hourly=None),
        res(resource_id="healthy", cpu=[60.0] * 720),
    ]
    ranked = rank_recommendations(assess_all(rs, thresholds, pricing))
    order = [r.assessment.resource_id for r in ranked]
    assert order == ["big", "small", "noprice", "prod_risk", "medium_q"]
    assert [r.rank for r in ranked] == [1, 2, 3, 4, 5]
    reasons = [r.reason for r in ranked]
    assert "lower estimated savings" in reasons[1]
    assert "cannot be estimated" in reasons[2]
    assert "higher risk" in reasons[3]
    assert "weaker evidence" in reasons[4]


def test_ranking_tie_broken_by_id(thresholds, pricing):
    ranked = rank_recommendations(
        assess_all([res(resource_id="b"), res(resource_id="a")], thresholds, pricing)
    )
    assert [r.assessment.resource_id for r in ranked] == ["a", "b"]
    assert "ordered by ID" in ranked[1].reason


def test_ranking_excludes_non_recommendations(thresholds, pricing):
    a = assess_all([res(cpu=[60.0] * 720), res(resource_id="x", days=5)], thresholds, pricing)
    assert rank_recommendations(a) == []
