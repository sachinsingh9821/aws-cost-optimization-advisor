"""Estimated compute cost per resource and in aggregate.

window_cost  = unit_price_hourly x running_hours            (cost over the observed window)
monthly_cost = unit_price_hourly x 730 x running_fraction   (monthly run-rate)

Scope: compute only. Excludes storage, data transfer, tax, credits and discounts.
Resources with an unknown price get NaN cost (never 0) and are counted as unpriced, so
totals are explicitly "for priced resources only".
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import pandas as pd

from src.config import Thresholds
from src.schema import Resource

COST_COLUMNS = [
    "resource_id",
    "service",
    "region",
    "resource_type",
    "instance_type",
    "environment",
    "unit_price_hourly",
    "running_hours",
    "window_hours",
    "priced",
    "window_cost_usd",
    "monthly_cost_usd",
]


@dataclass(frozen=True)
class CostSummary:
    total_monthly_usd: float  # priced resources only
    resource_count: int
    priced_count: int
    unpriced_ids: list[str]


def resource_costs(resources: Iterable[Resource], thresholds: Thresholds) -> pd.DataFrame:
    rows = []
    seen: set[str] = set()
    hpm = thresholds.analysis.hours_per_month
    for r in resources:
        if r.resource_id in seen:
            raise ValueError(f"duplicate resource_id {r.resource_id!r}")
        seen.add(r.resource_id)
        price = r.unit_price_hourly
        rows.append(
            {
                "resource_id": r.resource_id,
                "service": r.service,
                "region": r.region,
                "resource_type": r.resource_type,
                "instance_type": r.instance_type,
                "environment": str(r.environment),
                "unit_price_hourly": price,
                "running_hours": r.running_hours,
                "window_hours": r.window_hours,
                "priced": price is not None,
                "window_cost_usd": price * r.running_hours if price is not None else None,
                "monthly_cost_usd": (
                    price * hpm * r.running_hours / r.window_hours if price is not None else None
                ),
            }
        )
    df = pd.DataFrame(rows, columns=COST_COLUMNS)
    # Explicit dtype: an empty frame would otherwise give an object column, and pandas
    # would treat costs[costs["priced"]] as column selection instead of a row mask.
    df["priced"] = df["priced"].astype(bool)
    for col in ("unit_price_hourly", "window_cost_usd", "monthly_cost_usd"):
        df[col] = pd.to_numeric(df[col]).astype(float)  # None -> NaN, never 0
    return df


def cost_summary(costs: pd.DataFrame) -> CostSummary:
    priced = costs[costs["priced"]]
    return CostSummary(
        total_monthly_usd=round(float(priced["monthly_cost_usd"].sum()), 2),
        resource_count=len(costs),
        priced_count=len(priced),
        unpriced_ids=sorted(costs.loc[~costs["priced"], "resource_id"]),
    )


def cost_breakdown(costs: pd.DataFrame, by: str) -> pd.DataFrame:
    """Monthly cost grouped by a column (e.g. service, region, instance_type).

    min_count=1 makes a group whose every price is unknown sum to NaN instead of 0.
    """
    if by not in costs.columns:
        raise ValueError(f"unknown breakdown column {by!r}")
    grouped = costs.groupby(by, sort=True)
    out = pd.DataFrame(
        {
            "monthly_cost_usd": grouped["monthly_cost_usd"].sum(min_count=1).round(2),
            "resources": grouped.size(),
            "unpriced_resources": grouped["priced"].apply(lambda s: int((~s).sum())),
        }
    ).reset_index()
    return out.sort_values("monthly_cost_usd", ascending=False, na_position="last").reset_index(
        drop=True
    )
