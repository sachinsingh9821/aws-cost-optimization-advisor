"""Data-quality gate and evidence-quality grading. Runs before every detection rule.

Evidence quality is defined by explicit criteria, not a score:
  HIGH   passes the gate AND >= 28 days AND >= 97% complete AND <= 24 h stale
  MEDIUM passes the gate but misses at least one HIGH criterion
  LOW    fails the gate (never produces an actionable recommendation)
"""

from __future__ import annotations

from dataclasses import dataclass

from src.config import Thresholds
from src.schema import Evidence, EvidenceQuality


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reasons: list[str]  # why it failed (empty when passed)
    quality: EvidenceQuality


def check_data_quality(ev: Evidence, thresholds: Thresholds) -> GateResult:
    dq = thresholds.data_quality
    reasons: list[str] = []

    if ev.points_present == 0:
        reasons.append("no CPU metrics available")
    if ev.staleness_hours is None:
        reasons.append("no observation timestamp")
    elif ev.staleness_hours < 0:
        reasons.append("last observation is after the analysis time (clock/data error)")
    elif ev.staleness_hours > dq.max_staleness_hours:
        reasons.append(
            f"stale: last observation {ev.staleness_hours:.0f} h ago "
            f"(limit {dq.max_staleness_hours:.0f} h)"
        )
    if ev.coverage_days < dq.min_coverage_days:
        reasons.append(
            f"window {ev.coverage_days:.1f} days < minimum {dq.min_coverage_days:.0f} days"
        )
    if ev.points_present > 0 and ev.completeness < dq.min_completeness:
        reasons.append(f"completeness {ev.completeness:.1%} < minimum {dq.min_completeness:.0%}")

    if reasons:
        return GateResult(False, reasons, EvidenceQuality.LOW)

    eq = thresholds.evidence_quality
    assert ev.staleness_hours is not None  # guaranteed by the checks above
    is_high = (
        ev.coverage_days >= eq.high_min_days
        and ev.completeness >= eq.high_min_completeness
        and ev.staleness_hours <= eq.high_max_staleness_hours
    )
    return GateResult(True, [], EvidenceQuality.HIGH if is_high else EvidenceQuality.MEDIUM)
