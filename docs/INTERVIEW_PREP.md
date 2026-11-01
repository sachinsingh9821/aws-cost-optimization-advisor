# Interview prep

Short answers grounded in this repo. Numbers come from `reports/evaluation_report.md`
(v1 vs v2) and `reports/evaluation_report_v1.md` (frozen v1); file paths point to the code.

---

### 1. What does the project do, in one breath?
It looks at compute resources' CPU history and recommends two kinds of saving:
downsizing one size, and stopping non-prod boxes off-hours. It only does this when the
evidence is strong enough. It grades that evidence, attaches risks and assumptions, and
says `INSUFFICIENT_DATA` instead of guessing. A label-first evaluation measures the rules
on synthetic scenarios.

### 2. Why rules and not ML?
- There is no real labelled data. A model trained on my synthetic generator would just
  learn my generator.
- Cost actions need auditability: every decision here traces to a named rule, a threshold
  in `config/thresholds.yaml`, and measured evidence.
- The real problem is judgment under thin evidence. Explicit gates express that better
  than a probability.

### 3. Why mean **and** p95? Why not max?
One size down in the same family halves vCPUs, so CPU % roughly doubles.
- **Mean < 10%** → about 20% after downsizing. But the mean hides bursts: a box idling at
  3% with 10% of hours at 80% averages about 10%.
- **p95 < 30%** → about 60% after downsizing, leaving headroom under a typical 70–80%
  alarm. p95 ignores the busiest 5% of hours (~36 h a month), so a single noisy spike
  does not block a valid saving.
- **Max** is one hour, which is too sensitive to use as a gate. But p95 has a blind spot,
  so max came back as a *safety* check (see Q8).

5 of the 8 bursty synthetic resources average under 10% CPU (p95 69–79%); only p95
stops them being flagged (`tests/test_safety.py::test_bursty_resources_not_flagged_for_rightsizing`).

### 4. Why is missing data never zero?
A gap in CloudWatch means "we don't know", not "idle". Treating gaps as 0% CPU would make
broken monitoring look like an idle server and recommend downsizing it. So statistics use
present points only, completeness has to reach 90% before the gate passes, unknown prices
give `NaN` cost (not $0), and the dashboard draws gaps, not zeros. These rules are tested:
`test_evidence_ignores_missing_points_instead_of_zeroing`, `test_missing_metrics_never_treated_as_idle`.

### 5. How is evidence quality defined? Isn't it arbitrary?
It uses explicit criteria, not a score. **LOW** means the data fails the gate (< 14 days,
< 90% complete, > 48 h stale, or no metrics); LOW never produces a recommendation.
**HIGH** needs ≥ 28 days, ≥ 97% complete, and ≤ 24 h stale. Everything else that passes
is **MEDIUM**. 14 days is two weekly cycles; 28 days is four. The evaluation scored these
grades 88/88 correct.

### 6. You deviated from the spec on critical resources. Why?
The spec said "critical → `NEEDS_HUMAN_REVIEW`" inside the gate, i.e. *every* critical
resource goes to review. I check criticality **after** the rule signal instead:
critical + low usage gives review, and critical + busy gives `NO_ACTION`.

Reasons: a review queue full of healthy resources trains people to ignore it, and the
real safety property ("critical never gets an automatic `RECOMMEND`") holds either way. It
is tested, documented as a deliberate deviation in `docs/DESIGN.md` §5, and was decided
before any evaluation run. The trade-off: a critical resource whose problem is invisible
to CPU gets `NO_ACTION`, but with CPU-only data it would not have produced a finding anyway.

### 7. How did you avoid grading your own homework?
- **Labels first.** Ground truth was written from scenario definitions and committed
  (`37d481d`) before any rule code existed.
- **Opaque data.** Scenario names live in a separate manifest; the analysed data has IDs
  like `ec2-0042`, so rules cannot read the answer (enforced by a test).
- **Pre-registered predictions.** Before writing the evaluation code, I committed which
  cases I expected to miss and why (`f56984a`), with my prior knowledge disclosed.
- **Evaluation code frozen first**, then **one run**, committed exactly as generated.

Result: 3 misses out of 176 decisions, **all 3 pre-registered, 0 unpredicted**.

### 8. Tell me about the misses and what you changed.
- **P1, weekly batch.** Idle except a 4-hour Sunday job at 96%. That is 2.4% of hours,
  above the 95th percentile, so mean 5% and p95 4.4% both pass. The rule's own risk text
  said "projected peak ~192.5%" and still recommended. That is a false positive.
- **P2, borderline mean.** 11% mean, p95 13%: safe to downsize, but it misses the 10%
  cutoff. That is a false negative.
- **P3, dev box with nightly jobs.** Tagged schedule-eligible, but runs 01:00–04:00 jobs.
  The shutdown would break them. That is a false positive.

Root cause: **risk signals only re-ranked; they never changed the outcome.** I proposed:
- **C1:** 2 × max ≥ 100% → review.
- **C2:** off-hours mean > 5% → review.
- **C3:** relax the 10% cutoff. Rejected, because a missed saving is cheaper than a bad
  downsize.

### 9. How did you validate C1/C2 without overfitting?
I didn't re-score on the cases the fix was designed from. First I committed a
**held-out set** (29 resources, new seed) with cases the checks *should* catch *and*
near-misses they must not (max 45%, max 49% → projected 98%, off-hours noise 4%), plus
pre-registered v2 predictions (`b1c1b99`). Then I implemented the change.

Results: held-out misses went **15/58 → 0/58**; original-set RECOMMEND precision went to
**1.000** for both rules. The v1 report is reproduced byte-for-byte and pinned by SHA-256.

### 10. Did the fix break anything?
Yes, as predicted. `ec2-0070` is an idle box with a flat ~5.8% baseline at *every* hour.
C2 reads "off-hours > 5%" as "jobs run at night", so it goes to review: an over-trigger.
Original-set accuracy stayed at 3/176 misses, but the error *type* improved, from
actionable false positives to review items. The proper fix is a relative off-hours
check (C4), which needs its own held-out cases. It is not implemented yet.

### 11. What's the weakest part of your evaluation?
I wrote the scenarios, labels, rules **and** the held-out set. Non-ambiguous cases (v1
160/160) mostly show the code matches its spec. The ambiguous subset (**13/16** in v1,
14/16 in v2) is the informative number. The set is small (88 + 29 resources), and small
classes like 2 shutdown-review labels give noisy per-class metrics. The `ec2-0070`
over-trigger is a concrete example of what a same-author held-out set misses. None of it
says anything about real AWS accuracy.

### 12. Why precision/recall per class instead of just accuracy?
Accuracy hides *which* mistakes happen. Here a false `RECOMMEND` (it might break
production) is far worse than a false `NO_ACTION` (a missed saving). Per-class one-vs-rest
metrics plus a 4×4 confusion matrix show that directly. Zero denominators are reported as
`n/a`, not 0 or 1 (`src/evaluation/metrics.py`).

### 13. How do you check the savings numbers?
13 cases were hand-computed in `data/labels/expected_savings.csv` with the arithmetic
written out, including 2 unpriced cases where the expected answer is *no number*. The
engine matched all 13 to 4 decimal places. Percentage error is suppressed when the true
value is < $1, because it is meaningless near zero. Example: (0.20 − 0.10) × 730 = $73.00.

### 14. Estimated vs simulated vs realized savings?
- **Estimated** = a formula under stated assumptions (what this tool reports).
- **Simulated** = replaying a scenario after the action (not implemented).
- **Realized** = measured on real bills afterwards (not available; no real data).

The UI and docs keep the three apart, and every saving is labelled `kind="estimated"`.

### 15. Why a lexicographic ranking instead of a weighted score?
A weighted score needs invented weights and hides the trade-off. Sorting by evidence
quality, then risk, then savings means every rank has a one-line reason, e.g. *"#4: same
evidence, higher risk than the item above"* (`src/ranking/prioritize.py`).

### 16. How would this work on real AWS data?
- Read-only adapters: Cost Explorer for prices and cost, CloudWatch `GetMetricData` for
  hourly CPU, EC2/RDS describe calls for tags.
- Least-privilege IAM, mocked in tests with `botocore.Stubber`.
- Cost Explorer calls are billed, so cache them.
- Fail loudly instead of falling back to synthetic data.
- The rules stay the same, but the **thresholds would need re-validation on labelled real
  decisions**, e.g. past rightsizing tickets and whether they caused incidents.
- Add memory metrics (CloudWatch agent) before trusting rightsizing on memory-bound workloads.

### 17. How do you keep results reproducible?
- Fixed seeds (42 / 2026).
- A fixed `as_of` timestamp instead of `now()`.
- Pinned dependencies.
- CI regenerates data and labels and fails on any byte difference.
- Tests assert that the committed reports equal a fresh run.

### 18. Why is `app.py` "logic-free", and how is the UI tested?
All data shaping is in `src/dashboard/view_model.py` (unit tested). `app.py` only lays
out Streamlit widgets. Streamlit's headless `AppTest` renders every page and exercises
the filters and drill-down in CI, so a UI regression fails the build.

### 19. What would you do next?
- Fix the C2 over-trigger with a relative off-hours check (C4), pre-registered on new held-out cases.
- Add memory metrics.
- Deploy the synthetic demo.
- Add cost anomaly detection, kept separate from waste detection.
- Build read-only AWS adapters.
- Optionally, an LLM "explain this recommendation" that only rephrases the JSON, with a
  test that every number in the output appears in the input.

### 20. What did you learn?
Most of the work is deciding **when not to recommend** and being able to show how you know.
Writing labels and predictions *before* results made the evaluation far more convincing
than a high score. The one bug the held-out set missed taught me more than the 0/58 did.
