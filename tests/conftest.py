"""Shared test fixtures and a factory for hand-built resources."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from src.config import Pricing, Thresholds, load_pricing, load_thresholds

AS_OF = datetime(2026, 10, 31, tzinfo=UTC)


@pytest.fixture(scope="session")
def thresholds() -> Thresholds:
    return load_thresholds()


@pytest.fixture(scope="session")
def pricing() -> Pricing:
    return load_pricing()


def make_raw(
    *,
    cpu: list[float | None] | None = None,
    days: int = 30,
    end_offset_hours: int = 0,
    **overrides: Any,
) -> dict[str, Any]:
    """Build a valid raw resource dict; override any field via kwargs."""
    observed_to = AS_OF - timedelta(hours=end_offset_hours)
    observed_from = observed_to - timedelta(days=days)
    hours = days * 24
    if cpu is None and "cpu_hourly" not in overrides:
        cpu = [5.0] * hours
    raw: dict[str, Any] = {
        "resource_id": "ec2-test",
        "service": "EC2",
        "resource_type": "ec2_instance",
        "region": "us-east-1",
        "instance_type": "m5.xlarge",
        "environment": "dev",
        "criticality": "standard",
        "schedule_eligible": False,
        "cpu_hourly": cpu,
        "network_out_gb_daily_mean": 1.0,
        "running_hours": float(hours),
        "unit_price_hourly": 0.20,
        "provenance": "synthetic",
        "observed_from": observed_from.isoformat(),
        "observed_to": observed_to.isoformat(),
        "last_observed_at": (observed_to - timedelta(hours=1)).isoformat(),
    }
    raw.update(overrides)
    return raw
