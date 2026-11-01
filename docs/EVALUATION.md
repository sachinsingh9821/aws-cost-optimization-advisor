# Evaluation

> **SYNTHETIC DATA.** This evaluation validates rule *logic* on deliberately constructed
> synthetic scenarios. It is not evidence of real-world accuracy.
> Full generated results: [`reports/evaluation_report.md`](../reports/evaluation_report.md)
> (`python tasks.py evaluate`).

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
| next commit | First and only evaluation run, results committed unchanged |

**Prior knowledge, disclosed.** Before pre-registering I had seen per-scenario CPU
statistics (generator sanity check) and one smoke run's *total* outcome counts per rule.
I had not compared any rule output with labels per resource. Details are in
`data/labels/preregistered_predictions.yaml`.

**Post-run change, disclosed.** After the first run I added a "By subset" table
(ambiguous vs non-ambiguous) to the report. It is a new view of the same run: a diff of
the first-run report against the regenerated one shows only those added lines. No rule,
threshold, label or number changed.

## 3. Results (copied from the report)

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

## 4. Analysis of every miss

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

## 5. Proposed changes: NOT applied, awaiting approval

These are proposals only. The rules and thresholds that produced the results above are
unchanged. If any proposal is approved, the original results stay in this document, and
the change is validated on **new held-out scenarios written before it is applied**.
Re-scoring the same 8 ambiguous cases the change was designed from would be fitting to
the evaluation set.

| # | Proposal | Fixes | Recommendation |
|---|---|---|---|
| C1 | Rightsizing: if projected peak (2 × max) ≥ 100%, output `NEEDS_HUMAN_REVIEW` instead of `RECOMMEND` | P1 | **Adopt.** Principled (the smaller instance physically cannot serve the observed peak), and it reuses a signal already computed. Expected cost: some genuinely safe resources with one-off spikes go to review. |
| C2 | Shutdown: if off-hours mean CPU > 5%, output `NEEDS_HUMAN_REVIEW` instead of `RECOMMEND` | P3 | **Adopt.** It still never *infers* eligibility. It only refuses to act automatically when the data contradicts the tag. Turns an existing MEDIUM-risk flag into a gate. |
| C3 | Rightsizing: relax mean threshold, or use projected p95 only | P2 | **Do not adopt.** Missing a saving is cheap; a bad downsize is not. Keeping a conservative cutoff is the intended trade-off. Document it instead. |

## 6. Threshold change log

| Date | Change | Reason | Results before / after |
|---|---|---|---|
| — | none | — | — |

## 7. Limitations

- Same author for scenarios, labels and rules, so the non-ambiguous cases are close to
  self-consistency checks.
- Small hand-designed set (88 resources, 26 scenarios, 8 ambiguous). Small classes, such
  as 2 `NEEDS_HUMAN_REVIEW` shutdown labels, give very noisy per-class numbers.
- CPU only. Memory, disk and network effects of downsizing are not evaluated.
- Savings checks validate arithmetic against hand calculations, not real bills.
  Nothing here is a *realized* saving.
- No real AWS data. Real-world precision and recall are unknown.
