"""Everything the dashboard shows, computed here so app.py contains no business logic.

Pure functions over the engine's outputs. All figures are ESTIMATED from SYNTHETIC data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pandas as pd

from src.config import Pricing, Thresholds, load_pricing, load_thresholds
from src.cost.engine import CostSummary, cost_breakdown, cost_summary, resource_costs
from src.data.loader import DEFAULT_PATH, LoadResult, load_resources
from src.ranking.prioritize import RankedRecommendation, rank_recommendations
from src.rules.engine import assess_all
from src.rules.evidence import on_schedule_mask
from src.savings.estimate import total_estimated_savings
from src.schema import Assessment, Category, Outcome, Resource

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = REPO_ROOT / "reports" / "evaluation_report.md"
THRESHOLDS_PATH = REPO_ROOT / "config" / "thresholds.yaml"

SYNTHETIC_BANNER = (
    "SYNTHETIC DATA: every resource, metric and price in this dashboard is generated for "
    "demonstration (seed-fixed). Prices are illustrative, not current AWS pricing. Savings are "
    "estimated, never realized."
)
BREAKDOWN_DIMENSIONS = {
    "Service": "service",
    "Region": "region",
    "Instance type": "instance_type",
    "Environment": "environment",
}


@dataclass(frozen=True)
class Overview:
    total_monthly_cost_usd: float  # priced resources only
    resources_analyzed: int
    resources_rejected: int
    unpriced_resources: int
    estimated_monthly_savings_usd: float  # RECOMMEND only, no double counting
    pending_review_savings_usd: float  # NEEDS_HUMAN_REVIEW, not actionable
    actionable_recommendations: int
    needs_review: int
    insufficient_data: int
    recommendations_without_estimate: int


@dataclass(frozen=True)
class DashboardData:
    thresholds: Thresholds
    pricing: Pricing
    loaded: LoadResult
    assessments: list[Assessment]
    ranked: list[RankedRecommendation]
    costs: pd.DataFrame
    summary: CostSummary

    @property
    def resources_by_id(self) -> dict[str, Resource]:
        return {r.resource_id: r for r in self.loaded.resources}


def build(
    data_path: Path = DEFAULT_PATH,
    thresholds: Thresholds | None = None,
    pricing: Pricing | None = None,
) -> DashboardData:
    thresholds = thresholds or load_thresholds()
    pricing = pricing or load_pricing()
    loaded = load_resources(data_path)
    assessments = assess_all(loaded.resources, thresholds, pricing)
    costs = resource_costs(loaded.resources, thresholds)
    return DashboardData(
        thresholds=thresholds,
        pricing=pricing,
        loaded=loaded,
        assessments=assessments,
        ranked=rank_recommendations(assessments),
        costs=costs,
        summary=cost_summary(costs),
    )


def overview(d: DashboardData) -> Overview:
    recs = [a for a in d.assessments if a.is_recommendation]
    return Overview(
        total_monthly_cost_usd=d.summary.total_monthly_usd,
        resources_analyzed=d.summary.resource_count,
        resources_rejected=len(d.loaded.rejected),
        unpriced_resources=len(d.summary.unpriced_ids),
        estimated_monthly_savings_usd=total_estimated_savings(d.assessments),
        pending_review_savings_usd=total_estimated_savings(
            d.assessments, (Outcome.NEEDS_HUMAN_REVIEW,)
        ),
        actionable_recommendations=sum(a.outcome == Outcome.RECOMMEND for a in d.assessments),
        needs_review=sum(a.outcome == Outcome.NEEDS_HUMAN_REVIEW for a in d.assessments),
        insufficient_data=len(
            {a.resource_id for a in d.assessments if a.outcome == Outcome.INSUFFICIENT_DATA}
        ),
        recommendations_without_estimate=sum(
            a.savings is None or a.savings.monthly_usd is None for a in recs
        ),
    )


def outcome_counts(d: DashboardData) -> pd.DataFrame:
    """Rows: category; columns: outcome; values: number of resources."""
    df = pd.DataFrame(
        [{"category": str(a.category), "outcome": str(a.outcome)} for a in d.assessments]
    )
    table = df.pivot_table(index="category", columns="outcome", aggfunc="size", fill_value=0)
    return table.reindex(columns=[str(o) for o in Outcome], fill_value=0)


def breakdown(d: DashboardData, label: str) -> pd.DataFrame:
    return cost_breakdown(d.costs, BREAKDOWN_DIMENSIONS[label])


def recommendations_table(d: DashboardData) -> pd.DataFrame:
    """One row per ranked recommendation (RECOMMEND or NEEDS_HUMAN_REVIEW)."""
    by_id = d.resources_by_id
    rows = []
    for item in d.ranked:
        a = item.assessment
        r = by_id[a.resource_id]
        s = a.savings.monthly_usd if a.savings else None
        rows.append(
            {
                "rank": item.rank,
                "id": a.id,
                "resource_id": a.resource_id,
                "category": str(a.category),
                "outcome": str(a.outcome),
                "evidence_quality": str(a.evidence_quality),
                "risk": str(a.risk_level) if a.risk_level else "",
                "estimated_monthly_savings_usd": s,
                "environment": str(r.environment),
                "instance_type": r.instance_type,
                "proposed_instance_type": a.proposed_instance_type or "",
                "human_review_required": a.human_review_required,
                "rank_reason": item.reason,
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        df["estimated_monthly_savings_usd"] = pd.to_numeric(
            df["estimated_monthly_savings_usd"]
        ).astype(float)
    return df


def filter_recommendations(
    df: pd.DataFrame,
    categories: list[str] | None = None,
    outcomes: list[str] | None = None,
    qualities: list[str] | None = None,
    environments: list[str] | None = None,
) -> pd.DataFrame:
    """Empty or None filter = no restriction. Rank order is preserved."""
    mask = pd.Series(True, index=df.index)
    for col, allowed in [
        ("category", categories),
        ("outcome", outcomes),
        ("evidence_quality", qualities),
        ("environment", environments),
    ]:
        if allowed:
            mask &= df[col].isin(allowed)
    return df[mask]


def all_assessments_table(d: DashboardData) -> pd.DataFrame:
    """Every decision, including NO_ACTION and INSUFFICIENT_DATA, for auditability."""
    return pd.DataFrame(
        [
            {
                "id": a.id,
                "resource_id": a.resource_id,
                "category": str(a.category),
                "outcome": str(a.outcome),
                "evidence_quality": str(a.evidence_quality),
                "problem": a.problem,
            }
            for a in d.assessments
        ]
    )


def assessment_by_id(d: DashboardData, assessment_id: str) -> Assessment:
    for a in d.assessments:
        if a.id == assessment_id:
            return a
    raise KeyError(assessment_id)


def cpu_series(resource: Resource) -> pd.DataFrame:
    """Hourly CPU as a frame; missing points stay NaN so charts show gaps, not zeros."""
    if resource.cpu_hourly is None:
        return pd.DataFrame({"timestamp": pd.Series(dtype="datetime64[ns, UTC]"), "cpu_pct": []})
    stamps = [resource.observed_from + timedelta(hours=i) for i in range(resource.window_hours)]
    values = [float("nan") if v is None else v for v in resource.cpu_hourly]
    return pd.DataFrame({"timestamp": pd.to_datetime(stamps, utc=True), "cpu_pct": values})


def chart_references(
    d: DashboardData, a: Assessment
) -> tuple[list[tuple[str, float]], list[tuple[pd.Timestamp, pd.Timestamp]]]:
    """What to draw behind the CPU line: the thresholds the rule actually used.

    Rightsizing -> mean/p95 threshold lines. Shutdown -> shaded scheduled on-hours.
    """
    if a.category == Category.RIGHTSIZING:
        rs = d.thresholds.rightsizing
        return [("mean limit", rs.max_mean_cpu_pct), ("p95 limit", rs.max_p95_cpu_pct)], []
    r = d.resources_by_id[a.resource_id]
    mask = on_schedule_mask(r, d.thresholds.scheduled_shutdown)
    windows, run_start = [], None
    for i, on in enumerate([*mask, False]):
        if on and run_start is None:
            run_start = i
        elif not on and run_start is not None:
            windows.append(
                (
                    pd.Timestamp(r.observed_from + timedelta(hours=run_start)),
                    pd.Timestamp(r.observed_from + timedelta(hours=i)),
                )
            )
            run_start = None
    return [], windows


def evidence_rows(a: Assessment) -> pd.DataFrame:
    ev = a.evidence

    def fmt(x: float | None, unit: str = "%") -> str:
        return "not measurable" if x is None else f"{x:g}{unit}"

    rows = [
        ("Mean CPU", fmt(ev.mean_cpu_pct)),
        ("p95 CPU", fmt(ev.p95_cpu_pct)),
        ("Max CPU", fmt(ev.max_cpu_pct)),
        ("Off-hours mean CPU", fmt(ev.off_hours_mean_cpu_pct)),
        ("Observation window", f"{ev.coverage_days:g} days"),
        ("Completeness", f"{ev.completeness:.1%} ({ev.points_present}/{ev.points_expected} h)"),
        ("Staleness", fmt(ev.staleness_hours, " h")),
    ]
    return pd.DataFrame(rows, columns=["Evidence", "Value"])


def savings_rows(a: Assessment) -> pd.DataFrame:
    if a.savings is None:
        return pd.DataFrame(columns=["Item", "Value"])
    s = a.savings
    value = (
        f"${s.monthly_usd:,.2f} per month (estimated)"
        if s.monthly_usd is not None
        else s.cannot_estimate_reason or "Cannot estimate with available data"
    )
    rows = [("Estimated monthly savings", value), ("Formula", s.formula)]
    rows += [(f"Input: {k}", "unknown" if v is None else str(v)) for k, v in s.inputs.items()]
    return pd.DataFrame(rows, columns=["Item", "Value"])


def read_text(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.exists() else None
