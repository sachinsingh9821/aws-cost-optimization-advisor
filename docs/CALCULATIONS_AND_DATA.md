# Calculations and data

> **SYNTHETIC DATA.** Prices are *illustrative, not current AWS pricing*. Savings are
> *estimated* (a prediction under the assumptions below). Nothing here is *simulated*
> (replayed from a scenario after an action) or *realized* (measured on a real bill).

## 1. Units and constants

| Constant | Value | Where | Why |
|---|---|---|---|
| Hours per month | 730 | `thresholds.yaml: analysis.hours_per_month` | 24 × 365 / 12, the usual cloud billing convention |
| Analysis time (`as_of`) | 2026-10-31T00:00Z | `analysis.as_of` | Fixed, so staleness and results are reproducible |
| Schedule | Mon–Fri 09:00–19:00 UTC = 50 h/week | `scheduled_shutdown` | Typical working hours; documented, never inferred |
| Prices | USD per instance-hour | `pricing_illustrative.yaml` | Round numbers; each size up doubles vCPU and price |

## 2. Evidence (per resource)

Computed on **present** hourly points only. Missing points are excluded, never treated
as 0% CPU.

| Measure | Formula |
|---|---|
| coverage days | (observed_to − observed_from) / 24 h |
| completeness | present points / expected hourly points |
| staleness | as_of − last_observed_at (hours) |
| mean, max | over present points |
| p95 | 95th percentile of present points (NumPy linear interpolation) |
| off-hours mean | mean of present points outside the schedule |

**Why p95 and not max?** Max is a single hour, often noise. p95 ignores the busiest 5% of
hours (~36 h in a 30-day window). Its blind spot is regular peaks shorter than that,
which is why v2 also checks 2 × max (C1).

## 3. Data-quality gate and evidence grade

| Grade | Criteria |
|---|---|
| **LOW** (fails the gate → `INSUFFICIENT_DATA`) | no metrics, OR > 48 h stale, OR < 14 days, OR < 90% complete |
| **HIGH** | passes, AND ≥ 28 days, ≥ 97% complete, ≤ 24 h stale |
| **MEDIUM** | passes but misses a HIGH criterion |

Worked example: `ec2-0030` has its last point 47 h old and is 93.6% complete over 30 days.
It passes the gate (47 ≤ 48, 93.6 ≥ 90) but misses HIGH (47 > 24), so it gets **MEDIUM**.

## 4. Rules

**Rightsizing (RS-2):** mean < 10% **and** p95 < 30%. Going one size down in the same
family halves vCPUs, so CPU % roughly doubles: the projected mean stays under ~20% and the
projected p95 under ~60%. A critical resource, or a projected peak (2 × max) ≥ 100%,
gives `NEEDS_HUMAN_REVIEW`.

**Scheduled shutdown (SD-2):** environment ≠ prod **and** `schedule_eligible` is true.
A critical resource, or off-hours mean CPU > 5% (or unknown), gives `NEEDS_HUMAN_REVIEW`.

Full decision order: [`DESIGN.md` §4](DESIGN.md).

## 5. Cost

```
running_fraction      = running_hours / window_hours
window_cost           = unit_price × running_hours
monthly_cost          = unit_price × 730 × running_fraction
```
An unknown price gives `NaN` cost. The resource is listed as *unpriced* and excluded from
totals, never counted as $0. Breakdowns use `sum(min_count=1)`, so a group whose prices
are all unknown stays `NaN`.

Example: m5.xlarge at 0.20/h running 720 of 720 h → 0.20 × 730 × 1 = **$146.00/month**.

## 6. Savings (estimated)

```
rightsizing = (current_price − one_size_down_price) × 730 × running_fraction
shutdown    = current_price × max(0, 730 × running_fraction − 730 × 50 / 168)
```

| Resource | Calculation | Result |
|---|---|---|
| `ec2-0018` m5.xlarge → m5.large | (0.20 − 0.10) × 730 × 1 | $73.0000 |
| `ec2-0059` m5.4xlarge → m5.2xlarge | (0.80 − 0.40) × 730 × 1 | $292.0000 |
| `ec2-0003` r5.xlarge shutdown | off-hours = 730 × 118/168 = 512.7381 h; 0.26 × 512.7381 | $133.3119 |
| `ec2-0032` unpriced | price unknown | *Cannot estimate with available data* |

All 13 hand calculations are in `data/labels/expected_savings.csv` and are checked by
the evaluation (13/13 match).

**No double counting.** A resource can get both recommendations, but after downsizing, a
shutdown saves less. Aggregate savings therefore count only the **larger** estimate per
resource, which is a conservative lower bound.

**Assumptions** (attached to every recommendation): compute only (excludes storage, data
transfer, tax, credits, RIs/Savings Plans); illustrative prices; future load resembles
the observed window; one size down halves vCPU; stopped instances incur no compute charge.

## 7. Data dictionary (`data/synthetic/resources.json`)

| Field | Type | Notes |
|---|---|---|
| `resource_id` | str | opaque (`ec2-0042`); scenario names are **not** in this file |
| `service` | `EC2` \| `RDS` | |
| `resource_type` | `ec2_instance` \| `rds_instance` | |
| `region` | str | `us-east-1`, `eu-west-1`, `ap-south-1` |
| `instance_type` | str | from the illustrative ladder (m5, c5, r5, db.m5) |
| `environment` | `prod` \| `dev` \| `test` | |
| `criticality` | `standard` \| `critical` | |
| `schedule_eligible` | bool | owner-set tag; never inferred |
| `cpu_hourly` | list[float \| null] \| null | one value per hour from `observed_from`; null = missing |
| `network_out_gb_daily_mean` | float \| null | informational; not used by rules |
| `running_hours` | float | ≤ window hours |
| `unit_price_hourly` | float \| null | > 0; null = unknown |
| `provenance` | `synthetic` | the loader refuses data not labelled synthetic |
| `observed_from`, `observed_to`, `last_observed_at` | UTC datetime | whole-hour window |

Records with CPU outside 0–100, a wrong point count, inconsistent windows, unknown fields
or duplicate IDs are **rejected with a reason**, never repaired.

## 8. Provenance

| File | Produced by | Seed |
|---|---|---|
| `data/synthetic/resources.json`, `scenario_manifest.csv` | `python tasks.py data` | 42 |
| `data/heldout/resources.json`, `scenario_manifest.csv` | `python tasks.py data` | 2026 |
| `data/labels/ground_truth.csv`, `data/heldout/ground_truth.csv` | same, from hand-written scenario labels in `src/evaluation/labels.py` | — |
| `data/labels/expected_savings.csv` | hand-computed | — |

CI regenerates all of these and fails if any byte differs from the committed files.
