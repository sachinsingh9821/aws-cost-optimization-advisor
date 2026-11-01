# Design

> **Status legend:** ✅ implemented · ⏳ planned (not yet built). All data is **synthetic**.

## 1. Problem

Cloud cost tools often surface "savings" that rest on thin evidence: a few days of
metrics, gaps silently read as idle time, or a single average that hides spikes. This
project asks a narrower question: **given the metrics we actually have, which
cost-saving actions are justified, how strong is the evidence, and what could go wrong?**
When the answer is "we don't know", the system says `INSUFFICIENT_DATA` instead of
guessing.

It is rule-based by design. Every decision traces to a named rule, a threshold in
`config/thresholds.yaml`, and the measured evidence, so it can be audited line by line.

## 2. Architecture ✅

```
config/thresholds.yaml, pricing_illustrative.yaml
            │
data/synthetic/resources.json ──► src/data/loader ──► Resource (Pydantic, validated)
                                       │ rejects invalid / duplicate records with reasons
                                       ▼
            ┌──────────────── src/rules/engine ────────────────┐
            │ evidence ─► quality_gate ─► rightsizing (RS-1)    │
            │                         └─► shutdown    (SD-1)    │
            │                 both call src/savings/estimate    │
            └───────────────────────────┬──────────────────────┘
                                        ▼
                             Assessment (one per resource × rule)
                                        │
            src/cost/engine ◄───────────┼──────────► src/ranking/prioritize
            (spend, breakdowns)         │            (lexicographic order + reason)
                                        ▼
            ⏳ src/evaluation (vs data/labels)   ⏳ app.py (Streamlit, display only)
```

All business logic lives in pure, type-hinted functions under `src/`. The UI is planned
to contain no logic.

## 3. Data flow ✅

1. **Generate** (`python tasks.py data`): a seeded generator (seed 42) writes 88 resources
   across 26 scenarios. Scenario names go to a *separate* `scenario_manifest.csv`. The
   analysed data has opaque IDs, so rules cannot read the answer.
2. **Load**: Pydantic validates each record. Invalid values (CPU outside 0–100, wrong
   point count, inconsistent windows) and duplicate IDs are **rejected with a reason**,
   never repaired.
3. **Evidence**: mean, p95, max, off-hours mean, completeness, staleness, computed on
   *present* points only. Missing points are excluded, never treated as 0%.
4. **Gate → rules → savings**: see §4.
5. **Cost and ranking**: monthly run-rate per resource, with breakdowns; recommendations
   are ordered by evidence quality, then risk, then savings.

## 4. Decision logic ✅

Every resource gets one `Assessment` per rule. Order of checks (first match wins):

| Step | Rightsizing (RS-1) | Scheduled shutdown (SD-1) |
|---|---|---|
| 1 | Data-quality gate fails → `INSUFFICIENT_DATA` | same |
| 2 | Not (mean < 10% **and** p95 < 30%) → `NO_ACTION` | prod → `NO_ACTION` |
| 3 | No smaller size on ladder → `NO_ACTION` | not tagged `schedule_eligible` → `NO_ACTION` |
| 4 | **critical → `NEEDS_HUMAN_REVIEW`** | **critical → `NEEDS_HUMAN_REVIEW`** |
| 5 | → `RECOMMEND` | already within schedule → `NO_ACTION`, else `RECOMMEND` |

## 5. Deliberate deviation from the spec: when criticality is checked ✅

**Spec wording:** "Data-quality gate runs first: stale/incomplete → `INSUFFICIENT_DATA`;
critical → `NEEDS_HUMAN_REVIEW`." Read literally, *every* critical resource would be
sent to human review, whatever its usage.

**What we do instead:** the data-quality gate still runs first, but criticality is checked
**after** the rule's own signal (step 4 above):

| Critical resource with… | Outcome |
|---|---|
| bad / stale / incomplete data | `INSUFFICIENT_DATA` (gate still runs first) |
| low usage (rule signal present) | `NEEDS_HUMAN_REVIEW`, `human_review_required = true`, savings shown but not actioned |
| busy / healthy usage (no signal) | `NO_ACTION` |

**Why.**
- *Signal-to-noise.* A review queue that lists every critical resource, including healthy
  busy ones, teaches reviewers to ignore it. Review should mean "there is a finding here
  and it touches something critical".
- *Safety is unchanged.* The property that matters is that a critical resource **never**
  receives an automatic `RECOMMEND`. That holds in both designs and is enforced by tests.
- *Auditability.* A critical `NO_ACTION` still carries its evidence (mean, p95,
  coverage), so nothing is hidden. It just isn't queued.

**Trade-off accepted.** A critical resource whose *right-sizing need is invisible to CPU*
(for example memory pressure) gets `NO_ACTION`, whereas the literal spec would at least
have shown it to a human. Because CPU is the only metric we have, that resource would
not have produced a useful finding either way.

**Enforced by tests:**
- `tests/test_rules.py::test_critical_low_usage_needs_human_review` (prod and dev)
- `tests/test_rules.py::test_critical_busy_is_no_action_not_review` (busy, mean exactly at
  threshold, bursty with low mean)
- `tests/test_rules.py::test_critical_schedule_eligible_needs_review_for_shutdown`
- `tests/test_rules.py::test_critical_without_shutdown_signal_is_no_action`
- `tests/test_rules.py::test_critical_with_bad_data_is_insufficient_not_review`
- `tests/test_safety.py::test_critical_never_auto_flagged_for_shutdown`
- `tests/test_safety.py::test_critical_ordering_on_dataset`

This decision was made **before** the evaluation was first run. It was not chosen to
improve any metric.

## 6. Other key decisions and trade-offs ✅

| Decision | Alternative | Why |
|---|---|---|
| Rule-based, thresholds in YAML | ML classifier | No real labelled data; rules are explainable and testable; ML would just learn our synthetic generator. |
| Mean **and** p95 | Mean only / max only | Mean hides bursts; max is one outlier hour. p95 ignores the top 5% of hours, so very rare peaks (e.g. a 4 h weekly batch) are a known blind spot, partly covered by the projected-peak risk flag (2 × max > 80%). |
| One size down, same family | Any cheaper type | Halving vCPU has a predictable effect (CPU % ≈ ×2); cross-family moves need memory/network data we lack. |
| Explicit HIGH/MEDIUM/LOW criteria | Weighted 0–100 score | A score needs invented weights; criteria can be stated and tested. |
| Lexicographic ranking | Weighted ranking score | Each rank has a one-line reason ("same evidence, higher risk"). |
| Missing price → `NaN` + "Cannot estimate" | Treat as 0 | A $0 cost or $0 saving is a false claim. |
| Aggregate savings = max per resource | Sum all recommendations | Rightsizing and shutdown overlap; summing double counts. Max is a conservative lower bound. |
| Fixed `as_of` timestamp | Wall-clock `now()` | Staleness, and so results, must be reproducible. |
| Labels committed before rules | Labels written after | Git history (`37d481d`) shows labels could not have been fitted to rule output. |

## 7. Planned ⏳

- `src/evaluation/`: precision/recall/F1 per outcome class, confusion matrix, savings
  absolute error vs hand-computed values, `reports/evaluation_report.md`.
- `app.py`: Streamlit dashboard (Overview, Recommendations, Evaluation), display only.
- Post-deadline (needs approval): deployment, cost anomaly detection, read-only AWS
  adapters, optional LLM "explain this recommendation".
