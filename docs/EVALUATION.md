# Evaluation

> **SYNTHETIC DATA.** This evaluation validates rule *logic* on deliberately constructed
> synthetic scenarios. It is not evidence of real-world accuracy.
> Generated results (`python tasks.py evaluate`):
> current rules v2 with v1-vs-v2 comparison in
> [`reports/evaluation_report.md`](../reports/evaluation_report.md); frozen v1 results in
> [`reports/evaluation_report_v1.md`](../reports/evaluation_report_v1.md).

## 1. Methodology

**Unit of evaluation.** Each of the 88 resources gets one outcome per rule (rightsizing,
scheduled shutdown), giving **176 labelled decisions**. Each outcome is one of four
classes: `RECOMMEND`, `NO_ACTION`, `INSUFFICIENT_DATA`, `NEEDS_HUMAN_REVIEW`. Evidence
quality (`HIGH`/`MEDIUM`/`LOW`) is evaluated separately, once per resource.

**Metrics.**
- Per-class one-vs-rest precision, recall and F1. Zero denominators are reported as
  `n/a` (undefined), never as 0 or 1.
- 4×4 confusion matrix per rule (rows = label, columns = rule output).
- Savings: absolute error (USD/month) against 13 hand-computed cases, 2 of which
  expect *no number* because the price is unknown. Percentage error is shown only when
  the true value is ≥ 1 USD.
- 8 safety invariants, also enforced as tests in `tests/test_safety.py`.

## 2. Process: what was fixed before results existed

The order is visible in Git history:

| Commit | What was frozen |
|---|---|
| `37d481d` | Ground-truth labels and hand-computed savings, **before any rule code** |
| `4fe6e88`, `098b384` | Rules, savings, ranking (written to the spec, not to the labels) |
| `bc3c832` | Critical-resource ordering decision (see `DESIGN.md` §5) |
| `f56984a` | **Pre-registered predictions** of which cases the rules would miss |
| `c371c24` | Evaluation code, committed before its first run |
| `94a4d48` | First and only v1 evaluation run, results committed unchanged |

**Prior knowledge, disclosed.** Before pre-registering I had seen per-scenario CPU
statistics (generator sanity check) and one smoke run's *total* outcome counts per rule.
I had not compared any rule output with labels per resource. Details are in
`data/labels/preregistered_predictions.yaml`.

**Post-run change, disclosed.** After the first run I added a "By subset" table
(ambiguous vs non-ambiguous) to the report. It is a new view of the same run: a diff of
the first-run report against the regenerated one shows only those added lines. No rule,
threshold, label or number changed.

## 3. Results, rules v1 (frozen; copied from `evaluation_report_v1.md`)

| Rule | Accuracy | RECOMMEND precision | RECOMMEND recall | RECOMMEND F1 |
|---|---|---|---|---|
| Rightsizing | 0.977 (86/88) | 0.955 | 0.955 | 0.955 |
| Scheduled shutdown | 0.989 (87/88) | 0.933 | 1.000 | 0.966 |

| Subset | Decisions | Correct | Accuracy |
|---|---|---|---|
| Non-ambiguous | 160 | 160 | 1.000 |
| Ambiguous / boundary | 16 | 13 | 0.812 |

- Evidence quality: 88/88 correct (71 HIGH, 2 MEDIUM, 15 LOW).
- Savings: 13/13 checks passed, max absolute error 0.000000 USD. Both hand values and
  engine output are rounded to 4 decimals, so "0" means *agreement to 4 decimal places*.
  The 2 unpriced cases correctly produced no number.
- Safety invariants: 0 violations across all 8.
- Misses: 3, all pre-registered. **0 unpredicted misses.**

**How to read this.** The perfect non-ambiguous score mostly shows that the code does
what its specification says: the same person wrote the scenarios, labels and rules. The
informative number is the ambiguous subset, **13/16**. Those are the cases where a
careful engineer's judgment and a simple threshold rule can disagree.

## 4. Analysis of every v1 miss

### P1: `ec2-0057` weekly batch, rightsizing false positive

- **Facts:** m5.xlarge, prod. Mean 5.0%, p95 4.4%, max 96.3%. The rule output
  `RECOMMEND` at MEDIUM risk, an estimated $73.00/month to move to m5.large.
- **Why:** the 4-hour Sunday batch is 2.4% of hours, so it sits entirely above the
  95th percentile. Both rule conditions pass.
- **Telling detail:** the rule's own risk text says *"~192.5% peak"* after downsizing.
  The system knew the smaller instance could not run the batch, but risk only re-ranks.
  It does not block.
- **Severity:** this is the worst kind of error. An actionable recommendation that would
  roughly double the batch's run time, or break its SLA, in production.

### P2: `ec2-0027` borderline mean, rightsizing false negative

- **Facts:** c5.4xlarge, prod. Mean 11.1%, p95 13.3%, max 15.1%. The rule output
  `NO_ACTION`.
- **Why:** the mean fails the strict `< 10%` condition by 1.1 points.
- **Cost:** a missed saving of (0.72 − 0.36) × 730 = **$262.80/month**. That is a hand
  calculation; the engine produced no estimate because no recommendation was made.
- **Severity:** low. A missed saving is cheap and reversible compared with a bad
  downsize. A hard cutoff always has cases just over the line.

### P3: `ec2-0017` dev box with nightly jobs, shutdown false positive

- **Facts:** c5.2xlarge, dev, tagged `schedule_eligible`. Off-hours mean CPU 13.6%
  (nightly 01:00–04:00 jobs at ~70%). The rule output `RECOMMEND` at MEDIUM risk, an
  estimated $184.59/month. The label is `NEEDS_HUMAN_REVIEW`.
- **Why:** SD-1 trusts the eligibility tag by design (the spec says never infer
  eligibility). The off-hours activity was detected and reported as a risk, but it does
  not change the outcome.
- **Severity:** medium. It is non-prod, but the shutdown would silently break the
  nightly jobs.

### Pattern

All three misses come from the same design choice: **risk signals inform ranking but
never change the outcome.** In P1 and P3 the system measured the danger correctly
(projected peak 192.5%, off-hours CPU 13.6%) and still said "act".

## 5. Proposed changes after v1

**Decision (2026-11-01): C1 and C2 approved, C3 rejected.** The text below is the
proposal as written before the decision; results of the change are in §6.

These were proposals only. The rules and thresholds that produced the results above are
unchanged. If any proposal is approved, the original results stay in this document, and
the change is validated on **new held-out scenarios written before it is applied**.
Re-scoring the same 8 ambiguous cases the change was designed from would be fitting to
the evaluation set.

| # | Proposal | Fixes | Recommendation |
|---|---|---|---|
| C1 | Rightsizing: if projected peak (2 × max) ≥ 100%, output `NEEDS_HUMAN_REVIEW` instead of `RECOMMEND` | P1 | **Adopt.** Principled (the smaller instance physically cannot serve the observed peak), and it reuses a signal already computed. Expected cost: some genuinely safe resources with one-off spikes go to review. |
| C2 | Shutdown: if off-hours mean CPU > 5%, output `NEEDS_HUMAN_REVIEW` instead of `RECOMMEND` | P3 | **Adopt.** It still never *infers* eligibility. It only refuses to act automatically when the data contradicts the tag. Turns an existing MEDIUM-risk flag into a gate. |
| C3 | Rightsizing: relax mean threshold, or use projected p95 only | P2 | **Do not adopt.** Missing a saving is cheap; a bad downsize is not. Keeping a conservative cutoff is the intended trade-off. Document it instead. |

## 6. Rules v2 (C1 + C2): results

### Process

| Commit | What was frozen |
|---|---|
| `b1c1b99` | **Held-out set** (29 resources, seed 2026, `ho-` IDs), its labels, and **pre-registered v2 predictions**, before any C1/C2 code |
| `4b57c0f` | C1/C2 implemented as config values (`null` = v1), unit tests, and the single v1/v2 run on both sets |

The held-out set contains cases each check **should** catch and near-miss cases it
**must not** catch, to measure over-triggering:

| Check | Should catch (label `NEEDS_HUMAN_REVIEW`) | Must not catch |
|---|---|---|
| C1 | daily 60-70% peak hour, month-end 90% burst, twice-weekly 80% batch, max exactly 50% | max 35-45%, max 49% (projected 98%), plain idle |
| C2 | nightly ETL, Saturday jobs, flat 20% around the clock | quiet nights, off-hours noise 4.00%, one 30% blip per week |

v1 is reproduced by the same code with C1/C2 disabled. A test pins the v1 report's
SHA-256, so the v1 results above cannot change silently.

### Results (copied from `evaluation_report.md`)

| Set | Rules | Rightsizing accuracy | Shutdown accuracy | Outcome misses |
|---|---|---|---|---|
| original | v1 | 0.977 (86/88) | 0.989 (87/88) | 3 / 176 |
| original | v2 | 0.977 (86/88) | 0.989 (87/88) | 3 / 176 |
| held-out | v1 | 0.724 (21/29) | 0.759 (22/29) | 15 / 58 |
| held-out | v2 | 1.000 (29/29) | 1.000 (29/29) | 0 / 58 |

| Set | Rules | Rightsizing RECOMMEND P/R/F1 | Shutdown RECOMMEND P/R/F1 |
|---|---|---|---|
| original | v1 | 0.955 / 0.955 / 0.955 | 0.933 / 1.000 / 0.966 |
| original | v2 | 1.000 / 0.955 / 0.977 | 1.000 / 0.929 / 0.963 |
| held-out | v1 | 0.467 / 1.000 / 0.636 | 0.500 / 1.000 / 0.667 |
| held-out | v2 | 1.000 / 1.000 / 1.000 | 1.000 / 1.000 / 1.000 |

Safety violations: 0 in all four runs. **Every pre-registered v2 prediction came true**,
and no original-set output changed beyond the four predicted ones.

### What changed on the original set

| Resource | v1 | v2 | Label | Effect |
|---|---|---|---|---|
| `ec2-0017` nightly jobs (P3) | RECOMMEND | NEEDS_HUMAN_REVIEW | NEEDS_HUMAN_REVIEW | **fixed** |
| `ec2-0057` weekly batch (P1) | RECOMMEND | NEEDS_HUMAN_REVIEW | NO_ACTION | still a miss, but now a **review FP instead of an actionable FP** |
| `ec2-0027` borderline mean (P2) | NO_ACTION | NO_ACTION | RECOMMEND | unchanged (C3 rejected) |
| `ec2-0070` idle schedulable box | RECOMMEND | NEEDS_HUMAN_REVIEW | RECOMMEND | **new miss: C2 over-trigger** |

Original-set accuracy is the same in v1 and v2 (3 misses each), but the *kind* of error
changed. v1's actionable false positives on RECOMMEND went from 2 to 0, so rightsizing
and shutdown RECOMMEND precision are both 1.000 in v2. The price is one extra review
item and one lost shutdown recommendation (recall 1.000 → 0.929). For a cost tool that
can break workloads, trading actionable errors for review items is the intended direction.

### The C2 over-trigger (`ec2-0070`), honestly

This idle box has a flat CPU baseline of about 5.8% at *all* hours. C2 reads "off-hours
mean > 5%" as "something runs off-hours", but here nothing does: it is the same idle
baseline as during the day. **An absolute off-hours threshold cannot tell an idle
baseline from real off-hours work.** The held-out set did not expose this, because its
"must not catch" cases were built with quiet off-hours under 5%. That is exactly the
same-author blind spot noted below.

A relative check would separate the two, for example off-hours mean well above the
resource's own quiet baseline, or above some fraction of on-hours load. That would be a
new change (C4). It is **not** implemented; it would need its own pre-registered held-out
cases, such as idle boxes with 6-8% flat baselines.

### Limitation specific to the held-out set

**The held-out scenarios and labels were written by the same author who designed C1 and
C2, knowing exactly what the checks test.** The held-out result (0/58 misses) shows the
checks behave as designed on new data and do not over-trigger on the near-miss cases
*I chose*. It is not an independent test of whether these are the right checks. The
`ec2-0070` over-trigger, found on the original set and not the held-out set, shows what
such a set can miss.

## 7. Rule and threshold change log

| Date | Change | Reason | Results before → after |
|---|---|---|---|
| 2026-11-01 | **C1** added: `rightsizing.review_if_projected_peak_pct: 100` (new review gate; no existing threshold value changed) | v1 miss P1: projected peak 192.5% still produced an actionable RECOMMEND | original 3/176 → 3/176 misses (P1 becomes review FP); held-out 15/58 → 0/58 (both C1+C2) |
| 2026-11-01 | **C2** added: `scheduled_shutdown.review_if_off_hours_cpu_pct: 5` (new review gate; no existing threshold value changed) | v1 miss P3: off-hours jobs did not block a shutdown | fixes P3; introduces `ec2-0070` over-trigger |
| 2026-11-01 | C3 (relax 10% mean) **rejected** | A missed saving is cheaper than a bad downsize | — |

## 8. Limitations

- Same author for scenarios, labels and rules, so the non-ambiguous cases are close to
  self-consistency checks.
- The held-out set was also written by the author of C1/C2, after the v1 results were
  known (see §6). It checks intended behaviour, not whether the checks are right.
- Small hand-designed set (88 resources, 26 scenarios, 8 ambiguous). Small classes, such
  as 2 `NEEDS_HUMAN_REVIEW` shutdown labels, give very noisy per-class numbers.
- CPU only. Memory, disk and network effects of downsizing are not evaluated.
- Savings checks validate arithmetic against hand calculations, not real bills.
  Nothing here is a *realized* saving.
- No real AWS data. Real-world precision and recall are unknown.
