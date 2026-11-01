"""Deterministic synthetic dataset generator.

SYNTHETIC DATA ONLY. Every resource is fabricated from a fixed seed to exercise one
named scenario. Prices come from config/pricing_illustrative.yaml (illustrative, not
current AWS pricing).

Outputs:
- data/synthetic/resources.json        what the advisor analyses (no scenario names)
- data/synthetic/scenario_manifest.csv resource_id -> scenario, used ONLY by evaluation

Run: python -m src.data.generator
"""

from __future__ import annotations

import csv
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from src.config import Pricing, Thresholds, load_pricing, load_thresholds

SEED = 42
GENERATOR_VERSION = "1.0"
DEFAULT_WINDOW_DAYS = 30
REGIONS = ["us-east-1", "eu-west-1", "ap-south-1"]
EC2_FAMILIES = ["m5", "c5", "r5"]
REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "data" / "synthetic"

Pattern = Callable[[np.random.Generator, np.ndarray, np.ndarray], np.ndarray]


def _exact_mask(rng: np.random.Generator, n: int, frac: float) -> np.ndarray:
    """Boolean mask with exactly round(frac * n) True values.

    Exact counts (not ``rng.random() < frac``) guarantee boundary scenarios such as
    "89% complete" really are 89%, instead of landing on either side by chance.
    """
    mask = np.zeros(n, dtype=bool)
    mask[rng.choice(n, size=round(frac * n), replace=False)] = True
    return mask


# ---------------------------------------------------------------------------
# CPU patterns. Each receives (rng, hour_of_day[], weekday[]) and returns CPU %.
# ---------------------------------------------------------------------------


def _low(rng: np.random.Generator, hod: np.ndarray, dow: np.ndarray) -> np.ndarray:
    """Persistently underutilized: ~2-6% with small noise."""
    base = rng.uniform(2.0, 6.0)
    return base + rng.normal(0, 1.2, hod.size)


def _normal(rng: np.random.Generator, hod: np.ndarray, dow: np.ndarray) -> np.ndarray:
    """Healthy utilization with a daily cycle: ~25-55% mean."""
    base = rng.uniform(25.0, 55.0)
    return base + 10 * np.sin((hod - 6) / 24 * 2 * np.pi) + rng.normal(0, 5, hod.size)


def _bursty(rng: np.random.Generator, hod: np.ndarray, dow: np.ndarray) -> np.ndarray:
    """Low baseline with frequent high spikes (7-12% of hours at 60-95%)."""
    cpu = rng.uniform(1.5, 3.5) + rng.normal(0, 0.8, hod.size)
    spikes = _exact_mask(rng, hod.size, rng.uniform(0.07, 0.12))
    cpu[spikes] = rng.uniform(60, 95, spikes.sum())
    return cpu


def _workhours(rng: np.random.Generator, hod: np.ndarray, dow: np.ndarray) -> np.ndarray:
    """Busy Mon-Fri 09:00-19:00 UTC, near idle otherwise (classic dev/test box)."""
    on = (dow < 5) & (hod >= 9) & (hod < 19)
    cpu = rng.uniform(1.0, 3.0) + rng.normal(0, 0.5, hod.size)
    cpu[on] = rng.uniform(30, 60) + rng.normal(0, 6, on.sum())
    return cpu


def _weekly_batch(rng: np.random.Generator, hod: np.ndarray, dow: np.ndarray) -> np.ndarray:
    """Idle except a Sunday 02:00-06:00 batch at ~95% (4 of 168 weekly hours)."""
    cpu = 3.0 + rng.normal(0, 0.8, hod.size)
    batch = (dow == 6) & (hod >= 2) & (hod < 6)
    cpu[batch] = 95.0 + rng.normal(0, 1.5, batch.sum())
    return cpu


def _borderline(rng: np.random.Generator, hod: np.ndarray, dow: np.ndarray) -> np.ndarray:
    """Steady ~11% with tight noise: slightly above the 10% mean threshold."""
    return 11.0 + rng.normal(0, 1.2, hod.size)


def _overnight_jobs(rng: np.random.Generator, hod: np.ndarray, dow: np.ndarray) -> np.ndarray:
    """Work-hours usage PLUS nightly 01:00-04:00 jobs at ~70% every day."""
    cpu = _workhours(rng, hod, dow)
    nightly = (hod >= 1) & (hod < 4)
    cpu[nightly] = 70.0 + rng.normal(0, 4, nightly.sum())
    return cpu


def _p95_at_threshold(rng: np.random.Generator, hod: np.ndarray, dow: np.ndarray) -> np.ndarray:
    """Low mean, but ~7% of hours at 31-40% so p95 lands just above 30%."""
    cpu = 4.0 + rng.normal(0, 1.0, hod.size)
    elevated = _exact_mask(rng, hod.size, 0.07)
    cpu[elevated] = rng.uniform(31, 40, elevated.sum())
    return cpu


# ---------------------------------------------------------------------------
# Scenario definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Scenario:
    name: str
    count: int
    pattern: Pattern | None  # None => no CPU metrics at all
    environments: tuple[str, ...] = ("prod", "dev", "test")
    criticality: str = "standard"
    schedule_eligible: bool = False
    service: str = "EC2"
    sizes: tuple[str, ...] = ("xlarge", "2xlarge", "4xlarge")
    window_days: int = DEFAULT_WINDOW_DAYS
    window_end_offset_hours: int = 0  # window ends this many hours before as_of
    trailing_null_hours: int = 0  # last N hours missing (agent stopped reporting)
    random_null_frac: float = 0.0  # fraction of hours randomly missing
    price_missing: bool = False


SCENARIOS: list[Scenario] = [
    Scenario("underutilized", 12, _low),
    Scenario("underutilized_schedulable", 3, _low, ("dev", "test"), schedule_eligible=True),
    Scenario("normal", 16, _normal),
    Scenario("bursty", 8, _bursty),
    Scenario("schedule_candidate", 10, _workhours, ("dev", "test"), schedule_eligible=True),
    Scenario("nonprod_not_tagged_eligible", 3, _workhours, ("dev", "test")),
    Scenario("critical_underutilized", 5, _low, ("prod",), criticality="critical"),
    Scenario(
        "critical_nonprod_schedulable",
        1,
        _workhours,
        ("test",),
        criticality="critical",
        schedule_eligible=True,
    ),
    Scenario("critical_normal", 2, _normal, ("prod",), criticality="critical", service="RDS"),
    Scenario("stale_window_ended", 3, _low, window_end_offset_hours=96),
    Scenario("stale_trailing_gap", 2, _low, trailing_null_hours=120),
    Scenario("incomplete_metrics", 3, _low, random_null_frac=0.30),
    Scenario(
        "incomplete_metrics_schedulable",
        1,
        _workhours,
        ("dev",),
        schedule_eligible=True,
        random_null_frac=0.30,
    ),
    Scenario("short_window", 3, _low, window_days=9),
    Scenario("no_metrics", 2, None),
    Scenario("missing_price_underutilized", 3, _low, price_missing=True),
    Scenario(
        "missing_price_schedulable",
        1,
        _workhours,
        ("dev",),
        schedule_eligible=True,
        price_missing=True,
    ),
    Scenario("smallest_size_underutilized", 2, _low, sizes=("large",)),
    # --- Deliberately ambiguous / boundary cases (expected labels documented in labels) ---
    Scenario("amb_weekly_batch", 1, _weekly_batch, ("prod",)),
    Scenario("amb_borderline_mean", 1, _borderline, ("prod",)),
    Scenario("amb_exact_14_days", 1, _low, ("dev",), window_days=14, random_null_frac=0.07),
    Scenario("amb_completeness_89", 1, _low, ("dev",), random_null_frac=0.11),
    Scenario("amb_dev_overnight_jobs", 1, _overnight_jobs, ("dev",), schedule_eligible=True),
    Scenario("amb_prod_underutilized_standard", 1, _low, ("prod",)),
    Scenario("amb_p95_at_threshold", 1, _p95_at_threshold, ("test",)),
    Scenario("amb_staleness_47h", 1, _low, ("dev",), trailing_null_hours=46),
]


# ---------------------------------------------------------------------------
# HELD-OUT scenarios (written before rule changes C1/C2 were implemented).
# Both cases the new checks SHOULD catch and cases they should NOT catch, to measure
# over-triggering. Different seed and ID prefix; never used to design thresholds.
# ---------------------------------------------------------------------------

HELDOUT_SEED = 2026
HELDOUT_OUT_DIR = REPO_ROOT / "data" / "heldout"


def _daily_peak_hour(rng: np.random.Generator, hod: np.ndarray, dow: np.ndarray) -> np.ndarray:
    """Idle, plus one hour every day (14:00) at 60-70%: 3.3% of hours, under p95's reach."""
    cpu = 3.0 + rng.normal(0, 0.8, hod.size)
    peak = hod == 14
    cpu[peak] = rng.uniform(60, 70, peak.sum())
    return cpu


def _single_burst(rng: np.random.Generator, hod: np.ndarray, dow: np.ndarray) -> np.ndarray:
    """Idle, plus one 6-hour burst at ~90% somewhere in the window (month-end job)."""
    cpu = 3.0 + rng.normal(0, 0.8, hod.size)
    start = int(rng.integers(24, hod.size - 30))
    cpu[start : start + 6] = 90.0 + rng.normal(0, 2, 6)
    return cpu


def _twice_weekly_batch(rng: np.random.Generator, hod: np.ndarray, dow: np.ndarray) -> np.ndarray:
    """Idle, plus Tue and Thu 03:00-06:00 at ~80% (6 of 168 weekly hours = 3.6%)."""
    cpu = 3.0 + rng.normal(0, 0.8, hod.size)
    batch = np.isin(dow, [1, 3]) & (hod >= 3) & (hod < 6)
    cpu[batch] = 80.0 + rng.normal(0, 2, batch.sum())
    return cpu


def _moderate_max(rng: np.random.Generator, hod: np.ndarray, dow: np.ndarray) -> np.ndarray:
    """Idle with 3% of hours at 35-45%: projected peak <= 90%, which still fits."""
    cpu = 4.0 + rng.normal(0, 0.8, hod.size)
    bumps = _exact_mask(rng, hod.size, 0.03)
    cpu[bumps] = rng.uniform(35, 45, bumps.sum())
    return cpu


def _fixed_max(value: float) -> Pattern:
    """Idle with exactly 5 hours at ``value``% (boundary cases for C1)."""

    def pattern(rng: np.random.Generator, hod: np.ndarray, dow: np.ndarray) -> np.ndarray:
        cpu = np.clip(3.0 + rng.normal(0, 0.8, hod.size), 0.5, 8.0)
        cpu[rng.choice(hod.size, size=5, replace=False)] = value
        return cpu

    return pattern


def _workhours_nightly_etl(
    rng: np.random.Generator, hod: np.ndarray, dow: np.ndarray
) -> np.ndarray:
    """Work-hours usage plus a nightly 02:00-04:00 ETL at ~60% every day."""
    cpu = _workhours(rng, hod, dow)
    etl = (hod >= 2) & (hod < 4)
    cpu[etl] = 60.0 + rng.normal(0, 3, etl.sum())
    return cpu


def _workhours_weekend_jobs(
    rng: np.random.Generator, hod: np.ndarray, dow: np.ndarray
) -> np.ndarray:
    """Work-hours usage plus Saturday 08:00-18:00 jobs at ~60%."""
    cpu = _workhours(rng, hod, dow)
    sat = (dow == 5) & (hod >= 8) & (hod < 18)
    cpu[sat] = 60.0 + rng.normal(0, 3, sat.sum())
    return cpu


def _flat_moderate(rng: np.random.Generator, hod: np.ndarray, dow: np.ndarray) -> np.ndarray:
    """Flat ~20% around the clock: genuinely used off-hours."""
    return 20.0 + rng.normal(0, 2, hod.size)


def _workhours_offhours_noise_4(
    rng: np.random.Generator, hod: np.ndarray, dow: np.ndarray
) -> np.ndarray:
    """Work-hours usage with off-hours idle baseline ~4% (agent/OS noise, under 5%)."""
    on = (dow < 5) & (hod >= 9) & (hod < 19)
    cpu = 4.0 + rng.normal(0, 0.4, hod.size)
    cpu[on] = rng.uniform(30, 60) + rng.normal(0, 6, on.sum())
    return cpu


def _workhours_weekly_blip(
    rng: np.random.Generator, hod: np.ndarray, dow: np.ndarray
) -> np.ndarray:
    """Quiet nights except one 30% hour per week (Sunday 23:00, e.g. a health check)."""
    cpu = _workhours(rng, hod, dow)
    blip = (dow == 6) & (hod == 23)
    cpu[blip] = 30.0
    return cpu


NONPROD = ("dev", "test")
HELDOUT_SCENARIOS: list[Scenario] = [
    # C1 SHOULD catch: idle on average, but peaks that one size down cannot serve.
    Scenario("ho_daily_peak_hour", 3, _daily_peak_hour),
    Scenario("ho_single_month_end_burst", 2, _single_burst),
    Scenario("ho_twice_weekly_batch", 2, _twice_weekly_batch),
    Scenario("ho_max_exactly_50", 1, _fixed_max(50.0)),
    # C1 should NOT catch: peaks that still fit after halving.
    Scenario("ho_moderate_max", 3, _moderate_max),
    Scenario("ho_max_49", 1, _fixed_max(49.0)),
    Scenario("ho_plain_idle", 3, _low),
    # C2 SHOULD catch: tagged eligible, but real off-hours load.
    Scenario("ho_dev_nightly_etl", 3, _workhours_nightly_etl, NONPROD, schedule_eligible=True),
    Scenario("ho_dev_weekend_jobs", 2, _workhours_weekend_jobs, NONPROD, schedule_eligible=True),
    Scenario("ho_test_flat_20pct", 2, _flat_moderate, NONPROD, schedule_eligible=True),
    # C2 should NOT catch: off-hours noise under 5%.
    Scenario("ho_dev_quiet_nights", 3, _workhours, NONPROD, schedule_eligible=True),
    Scenario(
        "ho_dev_offhours_noise_4pct",
        2,
        _workhours_offhours_noise_4,
        NONPROD,
        schedule_eligible=True,
    ),
    Scenario("ho_dev_weekly_blip", 2, _workhours_weekly_blip, NONPROD, schedule_eligible=True),
]


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _pick_instance_type(rng: np.random.Generator, sc: Scenario) -> str:
    size = str(rng.choice(sc.sizes))
    if sc.service == "RDS":
        return f"db.m5.{size if size != '4xlarge' else '2xlarge'}"
    family = str(rng.choice(EC2_FAMILIES))
    if family == "r5" and size == "4xlarge":  # r5 ladder tops out at 2xlarge
        size = "2xlarge"
    return f"{family}.{size}"


def _make_record(
    rng: np.random.Generator, sc: Scenario, as_of: datetime, pricing: Pricing
) -> dict[str, Any]:
    observed_to = as_of - timedelta(hours=sc.window_end_offset_hours)
    observed_from = observed_to - timedelta(days=sc.window_days)
    n_hours = sc.window_days * 24
    stamps = [observed_from + timedelta(hours=i) for i in range(n_hours)]
    hod = np.array([t.hour for t in stamps])
    dow = np.array([t.weekday() for t in stamps])

    instance_type = _pick_instance_type(rng, sc)
    cpu: list[float | None] | None
    last_observed: datetime | None
    if sc.pattern is None:
        cpu, last_observed = None, None
    else:
        values = np.clip(sc.pattern(rng, hod, dow), 0.0, 100.0).round(2)
        missing = np.zeros(n_hours, dtype=bool)
        if sc.random_null_frac:
            missing |= _exact_mask(rng, n_hours, sc.random_null_frac)
        if sc.trailing_null_hours:
            missing[-sc.trailing_null_hours :] = True
        cpu = [None if m else float(v) for v, m in zip(values, missing, strict=True)]
        present = np.flatnonzero(~missing)
        last_observed = stamps[present[-1]] if present.size else None

    return {
        "service": sc.service,
        "resource_type": "rds_instance" if sc.service == "RDS" else "ec2_instance",
        "region": str(rng.choice(REGIONS)),
        "instance_type": instance_type,
        "environment": str(rng.choice(sc.environments)),
        "criticality": sc.criticality,
        "schedule_eligible": sc.schedule_eligible,
        "cpu_hourly": cpu,
        "network_out_gb_daily_mean": round(float(rng.uniform(0.1, 50.0)), 2),
        "running_hours": float(n_hours),  # all synthetic resources run 24/7
        "unit_price_hourly": None if sc.price_missing else pricing.price_of(instance_type),
        "provenance": "synthetic",
        "observed_from": _iso(observed_from),
        "observed_to": _iso(observed_to),
        "last_observed_at": _iso(last_observed) if last_observed else None,
    }


def generate(
    seed: int = SEED,
    thresholds: Thresholds | None = None,
    pricing: Pricing | None = None,
    scenarios: list[Scenario] | None = None,
    id_prefix: str = "",
) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    """Return (resource records, [(resource_id, scenario)]). Same seed -> same output."""
    thresholds = thresholds or load_thresholds()
    pricing = pricing or load_pricing()
    rng = np.random.default_rng(seed)
    as_of = thresholds.analysis.as_of

    drafts: list[tuple[str, dict[str, Any]]] = []
    for sc in scenarios if scenarios is not None else SCENARIOS:
        for _ in range(sc.count):
            drafts.append((sc.name, _make_record(rng, sc, as_of, pricing)))

    # Shuffle so neither order nor ID reveals the scenario, then assign opaque IDs.
    order = rng.permutation(len(drafts))
    records, manifest = [], []
    counters = {"EC2": 0, "RDS": 0}
    for idx in order:
        scenario, rec = drafts[idx]
        counters[rec["service"]] += 1
        prefix = "ec2" if rec["service"] == "EC2" else "rds"
        rid = f"{id_prefix}{prefix}-{counters[rec['service']]:04d}"
        records.append({"resource_id": rid, **rec})
        manifest.append((rid, scenario))
    return records, manifest


def write_dataset(
    out_dir: Path = OUT_DIR,
    seed: int = SEED,
    scenarios: list[Scenario] | None = None,
    id_prefix: str = "",
) -> tuple[Path, Path]:
    records, manifest = generate(seed, scenarios=scenarios, id_prefix=id_prefix)
    out_dir.mkdir(parents=True, exist_ok=True)
    data_path = out_dir / "resources.json"
    metadata = {
        "synthetic": True,
        "label": "SYNTHETIC DATA - generated for demonstration; not real AWS resources",
        "pricing_label": "illustrative, not current AWS pricing",
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "resource_count": len(records),
    }
    # One resource per line keeps the file diff-friendly in Git.
    lines = [json.dumps(r, separators=(",", ":")) for r in records]
    with open(data_path, "w", encoding="utf-8", newline="\n") as f:
        f.write('{"metadata":' + json.dumps(metadata) + ',"resources":[\n')
        f.write(",\n".join(lines))
        f.write("\n]}\n")

    manifest_path = out_dir / "scenario_manifest.csv"
    with open(manifest_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["resource_id", "scenario"])
        w.writerows(sorted(manifest))
    return data_path, manifest_path


if __name__ == "__main__":
    data_path, manifest_path = write_dataset()
    print(f"Wrote {data_path} and {manifest_path} (SYNTHETIC, seed={SEED})")
    ho_data, ho_manifest = write_dataset(HELDOUT_OUT_DIR, HELDOUT_SEED, HELDOUT_SCENARIOS, "ho-")
    print(f"Wrote {ho_data} and {ho_manifest} (SYNTHETIC held-out, seed={HELDOUT_SEED})")
