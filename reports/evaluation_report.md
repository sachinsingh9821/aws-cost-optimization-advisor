# Evaluation Report (pre-registration stub)

> **SYNTHETIC DATA.** No evaluation has been run yet. This file currently contains only
> the pre-registered predictions. It will be overwritten by `python tasks.py evaluate`,
> which re-renders this section from `data/labels/preregistered_predictions.yaml`.

## Pre-registered predictions

Written and committed **before** the evaluation code existed and before any per-resource
comparison of rule output with ground-truth labels.

| ID | Resource | Scenario | Category | Label | Predicted rule output | Error type |
|---|---|---|---|---|---|---|
| P1 | ec2-0057 | amb_weekly_batch | rightsizing | NO_ACTION | RECOMMEND | false positive |
| P2 | ec2-0027 | amb_borderline_mean | rightsizing | RECOMMEND | NO_ACTION | false negative |
| P3 | ec2-0017 | amb_dev_overnight_jobs | scheduled_shutdown | NEEDS_HUMAN_REVIEW | RECOMMEND | FP (RECOMMEND) / FN (NEEDS_HUMAN_REVIEW) |

Other claims: no additional outcome misses; no evidence-quality misses; every
hand-computed saving within 0.0001 USD; both unpriced cases yield no number; 0 safety
violations.

**Prior knowledge disclosed:** per-scenario CPU statistics from the generator sanity
check, and one smoke run's *total* outcome counts per category (no per-resource label
comparison). Full reasoning is in the YAML file.
