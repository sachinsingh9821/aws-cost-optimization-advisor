"""Dashboard view-model and chart builders (the UI itself holds no logic)."""

from __future__ import annotations

import math

import pytest

from src.dashboard import charts
from src.dashboard import view_model as vm
from src.schema import Category, Outcome, Resource
from tests.conftest import make_raw


@pytest.fixture(scope="module")
def d():
    return vm.build()


def test_overview_matches_engine(d):
    o = vm.overview(d)
    assert o.resources_analyzed == 88
    assert o.resources_rejected == 0
    assert o.unpriced_resources == 4
    assert o.actionable_recommendations == sum(
        a.outcome == Outcome.RECOMMEND for a in d.assessments
    )
    assert o.estimated_monthly_savings_usd > 0
    assert o.total_monthly_cost_usd == d.summary.total_monthly_usd


def test_outcome_counts_cover_every_decision(d):
    t = vm.outcome_counts(d)
    assert int(t.to_numpy().sum()) == len(d.assessments)
    assert list(t.columns) == [str(o) for o in Outcome]


def test_recommendations_table_ranked_and_unpriced_stays_nan(d):
    t = vm.recommendations_table(d)
    assert list(t["rank"]) == list(range(1, len(t) + 1))
    assert set(t["outcome"]) <= {"RECOMMEND", "NEEDS_HUMAN_REVIEW"}
    unpriced = set(d.summary.unpriced_ids)
    for _, row in t[t.resource_id.isin(unpriced)].iterrows():
        assert math.isnan(row["estimated_monthly_savings_usd"])


def test_filters(d):
    t = vm.recommendations_table(d)
    assert len(vm.filter_recommendations(t)) == len(t)
    only = vm.filter_recommendations(t, outcomes=["NEEDS_HUMAN_REVIEW"], environments=["prod"])
    assert set(only["outcome"]) == {"NEEDS_HUMAN_REVIEW"}
    assert set(only["environment"]) == {"prod"}
    assert list(only["rank"]) == sorted(only["rank"])


def test_breakdowns_all_dimensions(d):
    for label, col in vm.BREAKDOWN_DIMENSIONS.items():
        b = vm.breakdown(d, label)
        assert col in b.columns
        assert b["resources"].sum() == 88


def test_cpu_series_keeps_gaps():
    cpu = [5.0] * 720
    cpu[3] = None
    s = vm.cpu_series(Resource.model_validate(make_raw(cpu=cpu)))
    assert len(s) == 720
    assert math.isnan(s["cpu_pct"].iloc[3])


def test_cpu_series_no_metrics():
    s = vm.cpu_series(Resource.model_validate(make_raw(cpu_hourly=None, last_observed_at=None)))
    assert s.empty


def test_detail_rows(d):
    rec = next(a for a in d.assessments if a.outcome == Outcome.RECOMMEND and a.savings)
    ev = vm.evidence_rows(rec)
    assert "p95 CPU" in set(ev["Evidence"])
    sv = vm.savings_rows(rec)
    assert "(estimated)" in sv.iloc[0]["Value"] or "Cannot estimate" in sv.iloc[0]["Value"]
    no_action = next(a for a in d.assessments if a.outcome == Outcome.NO_ACTION)
    assert vm.savings_rows(no_action).empty
    assert vm.assessment_by_id(d, rec.id) is rec
    with pytest.raises(KeyError):
        vm.assessment_by_id(d, "nope")


def test_unpriced_detail_says_cannot_estimate(d):
    rec = next(
        a
        for a in d.assessments
        if a.is_recommendation and a.savings and a.savings.monthly_usd is None
    )
    assert "Cannot estimate" in vm.savings_rows(rec).iloc[0]["Value"]


def test_all_assessments_table(d):
    assert len(vm.all_assessments_table(d)) == len(d.assessments)


def test_banner_says_synthetic_and_illustrative():
    assert "SYNTHETIC DATA" in vm.SYNTHETIC_BANNER
    assert "illustrative, not current AWS pricing" in vm.SYNTHETIC_BANNER
    assert vm.read_text(vm.REPORT_PATH) is not None
    assert vm.read_text(vm.REPORT_PATH.parent / "missing.md") is None


# --- charts ------------------------------------------------------------------


def test_cost_bar_skips_unpriced_groups_and_uses_theme(d):
    df = vm.breakdown(d, "Service")
    df.loc[len(df)] = {
        "service": "UNPRICED",
        "monthly_cost_usd": float("nan"),
        "resources": 1,
        "unpriced_resources": 1,
    }
    fig = charts.cost_bar(df, "service", charts.DARK)
    assert "UNPRICED" not in list(fig.data[0].y)  # no misleading $0 bar
    assert fig.data[0].marker.color == charts.DARK.series
    assert fig.layout.paper_bgcolor == charts.DARK.surface


def test_cpu_line_draws_gaps_and_thresholds():
    s = vm.cpu_series(Resource.model_validate(make_raw()))
    fig = charts.cpu_line(s, [("mean limit", 10.0), ("p95 limit", 30.0)])
    assert fig.data[0].connectgaps is False
    assert len(fig.layout.shapes) == 2
    assert all(a.xref == "paper" and a.x == 1 for a in fig.layout.annotations)  # in margin


def test_chart_references_match_rule(d):
    rs = next(a for a in d.assessments if a.category == Category.RIGHTSIZING)
    lines, windows = vm.chart_references(d, rs)
    assert [v for _, v in lines] == [10.0, 30.0] and windows == []
    sd = next(a for a in d.assessments if a.category == Category.SCHEDULED_SHUTDOWN)
    lines, windows = vm.chart_references(d, sd)
    assert lines == []
    r = d.resources_by_id[sd.resource_id]
    hours = sum((e - s).total_seconds() / 3600 for s, e in windows)
    expected = vm.on_schedule_mask(r, d.thresholds.scheduled_shutdown).sum()
    assert hours == expected
    assert all(s.hour == 9 and e.hour == 19 for s, e in windows)


def test_theme_selection():
    assert charts.theme_for("dark") is charts.DARK
    assert charts.theme_for("light") is charts.LIGHT
    assert charts.theme_for(None) is charts.LIGHT
