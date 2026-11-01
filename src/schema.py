"""Pydantic models for input resources.

Design rules enforced here:
- Missing data stays missing: CPU points and prices are nullable, never defaulted to 0.
- Invalid values (CPU outside 0-100, negative hours, inconsistent windows) reject the
  record with a reason instead of being silently "fixed".
- Every record carries provenance so synthetic data can never be mistaken for real data.
"""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class Environment(StrEnum):
    PROD = "prod"
    DEV = "dev"
    TEST = "test"


class Criticality(StrEnum):
    STANDARD = "standard"
    CRITICAL = "critical"


class Resource(BaseModel):
    """One compute resource with its observation window and hourly CPU series.

    ``cpu_hourly[i]`` is the mean CPU % for the hour starting at
    ``observed_from + i hours``. ``None`` means the point is missing (not zero).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    resource_id: str = Field(min_length=1)
    service: Literal["EC2", "RDS"]
    resource_type: Literal["ec2_instance", "rds_instance"]
    region: str = Field(min_length=1)
    instance_type: str = Field(min_length=1)
    environment: Environment
    criticality: Criticality
    schedule_eligible: bool
    cpu_hourly: list[float | None] | None
    network_out_gb_daily_mean: float | None = Field(default=None, ge=0)
    running_hours: float = Field(ge=0)
    unit_price_hourly: float | None = Field(gt=0)
    provenance: Literal["synthetic"]
    observed_from: AwareDatetime
    observed_to: AwareDatetime
    last_observed_at: AwareDatetime | None

    @property
    def window_hours(self) -> int:
        return int((self.observed_to - self.observed_from) / timedelta(hours=1))

    @model_validator(mode="after")
    def _check_consistency(self) -> Resource:
        if self.observed_to <= self.observed_from:
            raise ValueError("observed_to must be after observed_from")
        window = self.observed_to - self.observed_from
        if window % timedelta(hours=1) != timedelta(0):
            raise ValueError("observation window must be a whole number of hours")
        if self.running_hours > self.window_hours:
            raise ValueError(
                f"running_hours ({self.running_hours}) exceeds window ({self.window_hours} h)"
            )
        if self.last_observed_at is not None and not (
            self.observed_from <= self.last_observed_at <= self.observed_to
        ):
            raise ValueError("last_observed_at must fall inside the observation window")
        if self.cpu_hourly is not None:
            if len(self.cpu_hourly) != self.window_hours:
                raise ValueError(
                    f"cpu_hourly has {len(self.cpu_hourly)} points, "
                    f"expected {self.window_hours} (one per hour)"
                )
            bad = [v for v in self.cpu_hourly if v is not None and not 0.0 <= v <= 100.0]
            if bad:
                raise ValueError(f"cpu_hourly contains values outside 0-100: {bad[:3]}")
        return self
