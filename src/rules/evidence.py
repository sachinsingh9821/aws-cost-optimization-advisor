"""Compute measurable evidence from a resource's hourly CPU series.

Statistics use only PRESENT points. Missing points are excluded, never counted as 0%;
completeness then tells the gate how much of the window those statistics represent.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np

from src.config import ScheduleConfig, Thresholds
from src.schema import Evidence, Resource


def on_schedule_mask(resource: Resource, schedule: ScheduleConfig) -> np.ndarray:
    """True for hours inside the documented on-schedule window (UTC)."""
    n = resource.window_hours
    start = resource.observed_from
    days = np.array([(start + timedelta(hours=i)).weekday() for i in range(n)])
    hours = np.array([(start + timedelta(hours=i)).hour for i in range(n)])
    return (
        np.isin(days, schedule.on_days)
        & (hours >= schedule.on_start_hour)
        & (hours < schedule.on_end_hour)
    )


def _round(x: float) -> float:
    return round(float(x), 2)


def compute_evidence(resource: Resource, thresholds: Thresholds) -> Evidence:
    as_of = thresholds.analysis.as_of
    expected = resource.window_hours
    staleness = (
        (as_of - resource.last_observed_at) / timedelta(hours=1)
        if resource.last_observed_at is not None
        else None
    )
    if resource.cpu_hourly is None:
        return Evidence(
            coverage_days=expected / 24,
            points_expected=expected,
            points_present=0,
            completeness=0.0,
            staleness_hours=staleness,
            mean_cpu_pct=None,
            p95_cpu_pct=None,
            max_cpu_pct=None,
            off_hours_mean_cpu_pct=None,
        )

    cpu = np.array([np.nan if v is None else v for v in resource.cpu_hourly], dtype=float)
    present = ~np.isnan(cpu)
    n_present = int(present.sum())
    off = ~on_schedule_mask(resource, thresholds.scheduled_shutdown) & present

    return Evidence(
        coverage_days=expected / 24,
        points_expected=expected,
        points_present=n_present,
        completeness=n_present / expected,
        staleness_hours=staleness,
        # p95 uses linear interpolation between ranks (NumPy default).
        mean_cpu_pct=_round(cpu[present].mean()) if n_present else None,
        p95_cpu_pct=_round(np.percentile(cpu[present], 95)) if n_present else None,
        max_cpu_pct=_round(cpu[present].max()) if n_present else None,
        off_hours_mean_cpu_pct=_round(cpu[off].mean()) if off.any() else None,
    )
