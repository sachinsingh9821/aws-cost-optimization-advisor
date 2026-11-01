"""Typed loading of config/thresholds.yaml and config/pricing_illustrative.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AnalysisConfig(_Strict):
    as_of: AwareDatetime
    hours_per_month: float = Field(gt=0)


class DataQualityConfig(_Strict):
    max_staleness_hours: float = Field(gt=0)
    min_coverage_days: float = Field(gt=0)
    min_completeness: float = Field(gt=0, le=1)


class EvidenceQualityConfig(_Strict):
    high_min_days: float = Field(gt=0)
    high_min_completeness: float = Field(gt=0, le=1)
    high_max_staleness_hours: float = Field(gt=0)


class RightsizingConfig(_Strict):
    max_mean_cpu_pct: float = Field(ge=0, le=100)
    max_p95_cpu_pct: float = Field(ge=0, le=100)
    projected_peak_risk_pct: float = Field(ge=0)
    review_if_projected_peak_pct: float | None = Field(default=None, gt=0)  # C1; None = v1


class ScheduleConfig(_Strict):
    timezone: str
    on_days: list[int]
    on_start_hour: int = Field(ge=0, le=23)
    on_end_hour: int = Field(ge=1, le=24)
    off_hours_activity_risk_pct: float = Field(ge=0, le=100)
    review_if_off_hours_cpu_pct: float | None = Field(default=None, ge=0, le=100)  # C2; None = v1

    @model_validator(mode="after")
    def _check(self) -> ScheduleConfig:
        if self.timezone != "UTC":
            raise ValueError("only UTC schedules are supported")
        if self.on_end_hour <= self.on_start_hour:
            raise ValueError("on_end_hour must be after on_start_hour")
        if not all(0 <= d <= 6 for d in self.on_days):
            raise ValueError("on_days must be weekday numbers 0-6")
        return self

    @property
    def on_hours_per_week(self) -> int:
        return len(set(self.on_days)) * (self.on_end_hour - self.on_start_hour)


class Thresholds(_Strict):
    analysis: AnalysisConfig
    data_quality: DataQualityConfig
    evidence_quality: EvidenceQualityConfig
    rightsizing: RightsizingConfig
    scheduled_shutdown: ScheduleConfig


class LadderStep(_Strict):
    size: str
    instance_type: str
    hourly_price: float = Field(gt=0)


class Pricing(_Strict):
    label: str
    currency: str
    unit: str
    families: dict[str, list[LadderStep]]

    def price_of(self, instance_type: str) -> float | None:
        for steps in self.families.values():
            for step in steps:
                if step.instance_type == instance_type:
                    return step.hourly_price
        return None

    def one_size_down(self, instance_type: str) -> LadderStep | None:
        """Next smaller size in the same family, or None if smallest/unknown."""
        for steps in self.families.values():
            for i, step in enumerate(steps):
                if step.instance_type == instance_type:
                    return steps[i - 1] if i > 0 else None
        return None


def as_v1(thresholds: Thresholds) -> Thresholds:
    """The same thresholds with C1 and C2 disabled: reproduces the v1 rules exactly."""
    return thresholds.model_copy(
        update={
            "rightsizing": thresholds.rightsizing.model_copy(
                update={"review_if_projected_peak_pct": None}
            ),
            "scheduled_shutdown": thresholds.scheduled_shutdown.model_copy(
                update={"review_if_off_hours_cpu_pct": None}
            ),
        }
    )


def load_thresholds(path: Path | None = None) -> Thresholds:
    path = path or CONFIG_DIR / "thresholds.yaml"
    with open(path, encoding="utf-8") as f:
        return Thresholds.model_validate(yaml.safe_load(f))


def load_pricing(path: Path | None = None) -> Pricing:
    path = path or CONFIG_DIR / "pricing_illustrative.yaml"
    with open(path, encoding="utf-8") as f:
        return Pricing.model_validate(yaml.safe_load(f))
