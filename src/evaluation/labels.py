"""Ground-truth labels, written from scenario DEFINITIONS before any detection rule existed.

Each label answers "what would a careful engineer conclude about this resource?", not
"what will the rules output?". Where the two may disagree (ambiguous cases), the
disagreement is the point: it shows up honestly as a false positive / false negative.

Outcome classes per category (rightsizing, scheduled shutdown):
    RECOMMEND           actionable recommendation expected
    NO_ACTION           nothing should be recommended
    INSUFFICIENT_DATA   evidence too weak/stale to decide
    NEEDS_HUMAN_REVIEW  signal present but a human must decide (e.g. critical resource)

Run: python -m src.evaluation.labels  -> writes data/labels/ground_truth.csv
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "data" / "synthetic" / "scenario_manifest.csv"
LABELS_PATH = REPO_ROOT / "data" / "labels" / "ground_truth.csv"
EXPECTED_SAVINGS_PATH = REPO_ROOT / "data" / "labels" / "expected_savings.csv"
HELDOUT_MANIFEST_PATH = REPO_ROOT / "data" / "heldout" / "scenario_manifest.csv"
HELDOUT_LABELS_PATH = REPO_ROOT / "data" / "heldout" / "ground_truth.csv"

OUTCOMES = ("RECOMMEND", "NO_ACTION", "INSUFFICIENT_DATA", "NEEDS_HUMAN_REVIEW")
QUALITIES = ("HIGH", "MEDIUM", "LOW")

REC, NOA, INS, HUM = OUTCOMES  # short aliases keep the label table readable


@dataclass(frozen=True)
class ScenarioLabel:
    rightsizing: str
    shutdown: str
    evidence_quality: str  # expected quality of the metric evidence itself
    ambiguous: bool
    rationale: str


SCENARIO_LABELS: dict[str, ScenarioLabel] = {
    "underutilized": ScenarioLabel(
        REC,
        NOA,
        "HIGH",
        False,
        "30 days of complete data, CPU ~2-6% with no peaks: one size down is safe. "
        "Not tagged schedule-eligible, so no shutdown.",
    ),
    "underutilized_schedulable": ScenarioLabel(
        REC,
        REC,
        "HIGH",
        False,
        "Idle non-prod box that is tagged schedule-eligible: both downsizing and an "
        "off-hours schedule are valid (savings must not be double counted).",
    ),
    "normal": ScenarioLabel(
        NOA, NOA, "HIGH", False, "Healthy 25-55% utilization; nothing to change."
    ),
    "bursty": ScenarioLabel(
        NOA,
        NOA,
        "HIGH",
        False,
        "Low average but 7-12% of hours spike to 60-95%. Halving capacity would "
        "saturate during spikes. Mean alone is misleading.",
    ),
    "schedule_candidate": ScenarioLabel(
        NOA,
        REC,
        "HIGH",
        False,
        "Busy in work hours (p95 well above 30%) so not oversized, but idle nights and "
        "weekends and tagged schedule-eligible: shutdown schedule is the right action.",
    ),
    "nonprod_not_tagged_eligible": ScenarioLabel(
        NOA,
        NOA,
        "HIGH",
        False,
        "Looks like a work-hours box, but nobody tagged it schedule-eligible. "
        "Eligibility is never inferred from usage.",
    ),
    "critical_underutilized": ScenarioLabel(
        HUM,
        NOA,
        "HIGH",
        False,
        "Underutilized, but critical: a human must approve any change. Prod, so no shutdown.",
    ),
    "critical_nonprod_schedulable": ScenarioLabel(
        NOA,
        HUM,
        "HIGH",
        False,
        "Tagged schedule-eligible in test, but marked critical: never auto-schedule a "
        "critical resource; route to human review.",
    ),
    "critical_normal": ScenarioLabel(
        NOA, NOA, "HIGH", False, "Critical and healthy; no signal, so nothing to review."
    ),
    "stale_window_ended": ScenarioLabel(
        INS,
        INS,
        "LOW",
        False,
        "Metrics stopped 97 h before analysis time. The resource may have changed or "
        "been terminated; data cannot support a decision.",
    ),
    "stale_trailing_gap": ScenarioLabel(
        INS, INS, "LOW", False, "No data for the last 5 days (stale, 83% complete)."
    ),
    "incomplete_metrics": ScenarioLabel(
        INS,
        INS,
        "LOW",
        False,
        "30% of hourly points missing. Missing is not zero; the gaps could hide peaks.",
    ),
    "incomplete_metrics_schedulable": ScenarioLabel(
        INS,
        INS,
        "LOW",
        False,
        "Tagged eligible, but 30% of points missing: we do not recommend anything for a "
        "resource we cannot observe properly (single data-quality gate for all rules).",
    ),
    "short_window": ScenarioLabel(
        INS, INS, "LOW", False, "Only 9 days observed; less than two weekly cycles."
    ),
    "no_metrics": ScenarioLabel(INS, INS, "LOW", False, "No CPU metrics at all."),
    "missing_price_underutilized": ScenarioLabel(
        REC,
        NOA,
        "HIGH",
        False,
        "Underutilization evidence is strong, so the recommendation stands, but savings "
        "must be reported as 'Cannot estimate with available data'.",
    ),
    "missing_price_schedulable": ScenarioLabel(
        NOA,
        REC,
        "HIGH",
        False,
        "Valid schedule candidate; savings cannot be estimated without a price.",
    ),
    "smallest_size_underutilized": ScenarioLabel(
        NOA,
        NOA,
        "HIGH",
        False,
        "Underutilized but already the smallest size on the ladder; one-size-down "
        "within the family is impossible (cross-family moves are out of scope).",
    ),
    # --- Ambiguous / boundary cases -------------------------------------------------
    "amb_weekly_batch": ScenarioLabel(
        NOA,
        NOA,
        "HIGH",
        True,
        "AMBIGUOUS. Idle except a 4 h Sunday batch at ~95% CPU (2.4% of hours). p95 "
        "misses it, but halving capacity would make the batch run at ~190% demand. "
        "A careful engineer would not downsize without checking the batch SLA.",
    ),
    "amb_borderline_mean": ScenarioLabel(
        REC,
        NOA,
        "HIGH",
        True,
        "AMBIGUOUS. Steady ~11% mean, p95 ~13%. Projected after halving: ~22% mean, "
        "~27% p95, which is safe. Genuinely oversized even though mean is just above 10%.",
    ),
    "amb_exact_14_days": ScenarioLabel(
        REC,
        NOA,
        "MEDIUM",
        True,
        "BOUNDARY. Exactly 14 days at ~93% completeness: meets the minimum bar, so a "
        "recommendation is expected, but only MEDIUM evidence (not a full month).",
    ),
    "amb_completeness_89": ScenarioLabel(
        INS,
        INS,
        "LOW",
        True,
        "BOUNDARY. 89% complete, just under the 90% policy minimum. Policy says "
        "insufficient, even though the visible data looks idle.",
    ),
    "amb_dev_overnight_jobs": ScenarioLabel(
        NOA,
        HUM,
        "HIGH",
        True,
        "AMBIGUOUS. Tagged schedule-eligible, but runs nightly 01:00-04:00 jobs at ~70% "
        "CPU. An off-hours shutdown would break them; the tag may be wrong. A human "
        "should confirm before scheduling.",
    ),
    "amb_prod_underutilized_standard": ScenarioLabel(
        REC,
        NOA,
        "HIGH",
        True,
        "AMBIGUOUS. Prod but not critical and clearly idle. Prod alone does not block "
        "rightsizing (only criticality does); prod does block shutdown.",
    ),
    "amb_p95_at_threshold": ScenarioLabel(
        NOA,
        NOA,
        "HIGH",
        True,
        "BOUNDARY. Mean ~6% but p95 ~33%: after halving, p95 would be ~66%. Not enough "
        "headroom to downsize confidently.",
    ),
    "amb_staleness_47h": ScenarioLabel(
        REC,
        NOA,
        "MEDIUM",
        True,
        "BOUNDARY. Last point 47 h old (limit 48 h), 93.6% complete, clearly idle. "
        "Passes the gate but freshness is not HIGH-grade, so MEDIUM evidence.",
    ),
}


# HELD-OUT labels: written with the held-out scenarios, BEFORE changes C1/C2 were coded.
# "Should catch" cases expect NEEDS_HUMAN_REVIEW; "should not catch" cases expect the
# v1 outcome, so over-triggering by the new checks shows up as a miss.
HELDOUT_SCENARIO_LABELS: dict[str, ScenarioLabel] = {
    "ho_daily_peak_hour": ScenarioLabel(
        HUM,
        NOA,
        "HIGH",
        False,
        "Idle on average, but a daily 60-70% hour would hit ~120-140% after halving. "
        "A human must check whether that hour can tolerate slowdown.",
    ),
    "ho_single_month_end_burst": ScenarioLabel(
        HUM,
        NOA,
        "HIGH",
        False,
        "One 6 h burst at ~90% (month-end job). Smaller size cannot serve it; review.",
    ),
    "ho_twice_weekly_batch": ScenarioLabel(
        HUM,
        NOA,
        "HIGH",
        False,
        "Tue/Thu batch at ~80-86% (3.6% of hours): invisible to p95, exceeds capacity "
        "after halving.",
    ),
    "ho_max_exactly_50": ScenarioLabel(
        HUM,
        NOA,
        "HIGH",
        True,
        "BOUNDARY. Peak exactly 50% -> ~100% after halving: no headroom at all at peak.",
    ),
    "ho_moderate_max": ScenarioLabel(
        REC,
        NOA,
        "HIGH",
        False,
        "Idle with occasional 35-45% hours: ~70-90% after halving still fits. Safe to "
        "downsize; C1 must NOT trigger.",
    ),
    "ho_max_49": ScenarioLabel(
        REC,
        NOA,
        "HIGH",
        True,
        "BOUNDARY. Peak 49% -> ~98% after halving: tight but fits; C1 must NOT trigger.",
    ),
    "ho_plain_idle": ScenarioLabel(
        REC, NOA, "HIGH", False, "Control: plainly idle, peak < 10%. Downsize."
    ),
    "ho_dev_nightly_etl": ScenarioLabel(
        NOA,
        HUM,
        "HIGH",
        False,
        "Tagged eligible, but a nightly ETL at ~60% would be killed by an off-hours "
        "shutdown. Tag may be stale; review.",
    ),
    "ho_dev_weekend_jobs": ScenarioLabel(
        NOA, HUM, "HIGH", False, "Saturday jobs at ~60% would break under the schedule; review."
    ),
    "ho_test_flat_20pct": ScenarioLabel(
        NOA,
        HUM,
        "HIGH",
        False,
        "Tagged eligible but used ~20% around the clock: someone relies on it off-hours.",
    ),
    "ho_dev_quiet_nights": ScenarioLabel(
        NOA, REC, "HIGH", False, "Control: busy work hours, idle nights. Schedule it."
    ),
    "ho_dev_offhours_noise_4pct": ScenarioLabel(
        NOA,
        REC,
        "HIGH",
        True,
        "BOUNDARY. Off-hours ~4% is OS/agent noise, not work; C2 must NOT trigger.",
    ),
    "ho_dev_weekly_blip": ScenarioLabel(
        NOA,
        REC,
        "HIGH",
        False,
        "One 30% hour per week (health check) does not justify running 118 h/week; "
        "C2 must NOT trigger.",
    ),
}


def read_manifest(path: Path = MANIFEST_PATH) -> dict[str, str]:
    with open(path, encoding="utf-8") as f:
        return {row["resource_id"]: row["scenario"] for row in csv.DictReader(f)}


def build_ground_truth(
    manifest: dict[str, str], scenario_labels: dict[str, ScenarioLabel] | None = None
) -> list[dict[str, str]]:
    scenario_labels = SCENARIO_LABELS if scenario_labels is None else scenario_labels
    rows = []
    for rid, scenario in sorted(manifest.items()):
        lab = scenario_labels[scenario]  # KeyError = unlabelled scenario, fail loudly
        rows.append(
            {
                "resource_id": rid,
                "scenario": scenario,
                "expected_rightsizing": lab.rightsizing,
                "expected_shutdown": lab.shutdown,
                "expected_evidence_quality": lab.evidence_quality,
                "ambiguous": str(lab.ambiguous).lower(),
                "rationale": lab.rationale,
            }
        )
    return rows


def write_ground_truth(
    path: Path = LABELS_PATH,
    manifest_path: Path = MANIFEST_PATH,
    scenario_labels: dict[str, ScenarioLabel] | None = None,
) -> Path:
    rows = build_ground_truth(read_manifest(manifest_path), scenario_labels)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return path


def read_ground_truth(path: Path = LABELS_PATH) -> list[dict[str, str]]:
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_expected_savings(path: Path = EXPECTED_SAVINGS_PATH) -> list[dict[str, str]]:
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


if __name__ == "__main__":
    print(f"Wrote {write_ground_truth()}")
    print(
        "Wrote "
        f"{write_ground_truth(HELDOUT_LABELS_PATH, HELDOUT_MANIFEST_PATH, HELDOUT_SCENARIO_LABELS)}"
    )
