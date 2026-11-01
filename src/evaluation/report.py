"""Evaluate rule output against ground-truth labels and write reports/evaluation_report.md.

Run: python -m src.evaluation.report   (or: python tasks.py evaluate)

This validates rule LOGIC on SYNTHETIC scenarios. It says nothing about accuracy on real
AWS workloads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml

from src.config import Pricing, Thresholds, load_pricing, load_thresholds
from src.data.loader import load_resources
from src.evaluation.labels import (
    OUTCOMES,
    QUALITIES,
    REPO_ROOT,
    read_expected_savings,
    read_ground_truth,
    read_manifest,
)
from src.evaluation.metrics import (
    SAVINGS_TOLERANCE_USD,
    ClassMetrics,
    SavingsCheck,
    accuracy,
    check_savings,
    class_metrics,
    confusion_matrix,
    safe_div,
)
from src.rules.engine import assess_all
from src.schema import Assessment, Category, Criticality, EvidenceQuality, Outcome, Resource

REPORT_PATH = REPO_ROOT / "reports" / "evaluation_report.md"
PREDICTIONS_PATH = REPO_ROOT / "data" / "labels" / "preregistered_predictions.yaml"

LABEL_COLUMN = {
    Category.RIGHTSIZING: "expected_rightsizing",
    Category.SCHEDULED_SHUTDOWN: "expected_shutdown",
}
CATEGORY_ALIASES = {
    "rightsizing": Category.RIGHTSIZING,
    "shutdown": Category.SCHEDULED_SHUTDOWN,
    "scheduled_shutdown": Category.SCHEDULED_SHUTDOWN,
}


@dataclass(frozen=True)
class Miss:
    resource_id: str
    scenario: str
    category: str  # rule category, or "evidence_quality"
    expected: str
    actual: str
    ambiguous: bool
    assessment: Assessment


@dataclass(frozen=True)
class CategoryResult:
    metrics: list[ClassMetrics]
    confusion: dict[str, dict[str, int]]
    accuracy: float | None
    n: int


@dataclass
class EvaluationResult:
    metadata: dict[str, Any]
    n_resources: int
    n_rejected: int
    thresholds: Thresholds
    categories: dict[Category, CategoryResult]
    quality: CategoryResult
    savings: list[SavingsCheck]
    safety: list[tuple[str, list[str]]]
    misses: list[Miss]
    predictions: dict[str, Any]
    prediction_rows: list[dict[str, str]] = field(default_factory=list)
    n_ambiguous_resources: int = 0


# ---------------------------------------------------------------------------
# Computation
# ---------------------------------------------------------------------------


def _safety_checks(
    resources: list[Resource],
    assessments: list[Assessment],
    manifest: dict[str, str],
    thresholds: Thresholds,
) -> list[tuple[str, list[str]]]:
    by_id = {r.resource_id: r for r in resources}
    high_stale = timedelta(hours=thresholds.evidence_quality.high_max_staleness_hours)
    as_of = thresholds.analysis.as_of

    def violations(pred) -> list[str]:
        return sorted(a.id for a in assessments if pred(by_id[a.resource_id], a))

    sd, rs = Category.SCHEDULED_SHUTDOWN, Category.RIGHTSIZING
    return [
        (
            "Critical resources never receive an automatic RECOMMEND (any rule)",
            violations(
                lambda r, a: (
                    r.criticality == Criticality.CRITICAL and a.outcome == Outcome.RECOMMEND
                )
            ),
        ),
        (
            "Critical resources never auto-flagged for shutdown",
            violations(
                lambda r, a: (
                    r.criticality == Criticality.CRITICAL
                    and a.category == sd
                    and a.outcome == Outcome.RECOMMEND
                )
            ),
        ),
        (
            f"Stale data (> {high_stale / timedelta(hours=1):g} h or no timestamp) never HIGH",
            violations(
                lambda r, a: (
                    (r.last_observed_at is None or as_of - r.last_observed_at > high_stale)
                    and a.evidence_quality == EvidenceQuality.HIGH
                )
            ),
        ),
        (
            "Unknown price never yields a savings number",
            violations(
                lambda r, a: (
                    r.unit_price_hourly is None
                    and a.savings is not None
                    and a.savings.monthly_usd is not None
                )
            ),
        ),
        (
            "Bursty resources not flagged for rightsizing",
            violations(
                lambda r, a: (
                    manifest[r.resource_id] == "bursty"
                    and a.category == rs
                    and a.outcome != Outcome.NO_ACTION
                )
            ),
        ),
        (
            "LOW evidence never produces a recommendation",
            violations(
                lambda r, a: a.evidence_quality == EvidenceQuality.LOW and a.is_recommendation
            ),
        ),
        (
            "Prod resources never scheduled off",
            violations(
                lambda r, a: r.environment == "prod" and a.category == sd and a.is_recommendation
            ),
        ),
        (
            "Untagged resources never scheduled off (eligibility not inferred)",
            violations(
                lambda r, a: not r.schedule_eligible and a.category == sd and a.is_recommendation
            ),
        ),
    ]


def _prediction_rows(predictions: dict[str, Any], misses: list[Miss]) -> list[dict[str, str]]:
    remaining = list(misses)
    rows = []
    for p in predictions.get("predicted_misses", []):
        cat = str(CATEGORY_ALIASES[p["category"]])
        match = next(
            (m for m in remaining if m.resource_id == p["resource_id"] and m.category == cat), None
        )
        if match is None:
            result, actual = "Did not occur", "(matched label)"
        else:
            remaining.remove(match)
            actual = match.actual
            result = (
                "Occurred as predicted"
                if match.actual == p["predicted_rule_output"]
                else "Occurred, but with a different output"
            )
        rows.append(
            {
                "id": p["id"],
                "resource_id": p["resource_id"],
                "scenario": p["scenario"],
                "category": cat,
                "label": p["expected_label"],
                "predicted": p["predicted_rule_output"],
                "actual": actual,
                "result": result,
            }
        )
    for m in remaining:
        rows.append(
            {
                "id": "-",
                "resource_id": m.resource_id,
                "scenario": m.scenario,
                "category": m.category,
                "label": m.expected,
                "predicted": "-",
                "actual": m.actual,
                "result": "NOT PREDICTED",
            }
        )
    return rows


def run_evaluation(
    thresholds: Thresholds | None = None, pricing: Pricing | None = None
) -> EvaluationResult:
    thresholds = thresholds or load_thresholds()
    pricing = pricing or load_pricing()
    loaded = load_resources()
    labels = {row["resource_id"]: row for row in read_ground_truth()}
    manifest = read_manifest()

    resource_ids = {r.resource_id for r in loaded.resources}
    if resource_ids != set(labels):
        raise ValueError("labelled resources and loaded resources differ; regenerate labels")

    assessments = assess_all(loaded.resources, thresholds, pricing)
    by_key = {(a.resource_id, a.category): a for a in assessments}
    ids = sorted(labels)

    categories: dict[Category, CategoryResult] = {}
    misses: list[Miss] = []
    for cat, col in LABEL_COLUMN.items():
        exp = [labels[i][col] for i in ids]
        pred = [str(by_key[(i, cat)].outcome) for i in ids]
        categories[cat] = CategoryResult(
            class_metrics(exp, pred, OUTCOMES),
            confusion_matrix(exp, pred, OUTCOMES),
            accuracy(exp, pred),
            len(ids),
        )
        for i, e, p in zip(ids, exp, pred, strict=True):
            if e != p:
                misses.append(
                    Miss(
                        i,
                        labels[i]["scenario"],
                        str(cat),
                        e,
                        p,
                        labels[i]["ambiguous"] == "true",
                        by_key[(i, cat)],
                    )
                )

    # Evidence quality depends only on the data, so it is identical for both rules.
    q_exp = [labels[i]["expected_evidence_quality"] for i in ids]
    q_pred = [str(by_key[(i, Category.RIGHTSIZING)].evidence_quality) for i in ids]
    quality = CategoryResult(
        class_metrics(q_exp, q_pred, QUALITIES),
        confusion_matrix(q_exp, q_pred, QUALITIES),
        accuracy(q_exp, q_pred),
        len(ids),
    )
    for i, e, p in zip(ids, q_exp, q_pred, strict=True):
        if e != p:
            misses.append(
                Miss(
                    i,
                    labels[i]["scenario"],
                    "evidence_quality",
                    e,
                    p,
                    labels[i]["ambiguous"] == "true",
                    by_key[(i, Category.RIGHTSIZING)],
                )
            )

    savings = []
    for row in read_expected_savings():
        cat = CATEGORY_ALIASES[row["category"]]
        a = by_key[(row["resource_id"], cat)]
        expected = (
            float(row["expected_monthly_savings_usd"])
            if row["expected_monthly_savings_usd"]
            else None
        )
        actual = a.savings.monthly_usd if a.savings else None
        savings.append(check_savings(row["resource_id"], str(cat), expected, actual))

    with open(PREDICTIONS_PATH, encoding="utf-8") as f:
        predictions = yaml.safe_load(f)

    result = EvaluationResult(
        metadata=loaded.metadata,
        n_resources=len(loaded.resources),
        n_rejected=len(loaded.rejected),
        thresholds=thresholds,
        categories=categories,
        quality=quality,
        savings=savings,
        safety=_safety_checks(loaded.resources, assessments, manifest, thresholds),
        misses=misses,
        predictions=predictions,
    )
    result.prediction_rows = _prediction_rows(predictions, misses)
    result.n_ambiguous_resources = sum(labels[i]["ambiguous"] == "true" for i in ids)
    return result


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _f(x: float | None, digits: int = 3) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}"


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def _metrics_table(res: CategoryResult) -> str:
    rows = [
        [m.label, m.support, m.predicted, m.tp, m.fp, m.fn, _f(m.precision), _f(m.recall), _f(m.f1)]
        for m in res.metrics
    ]
    return _table(
        ["Class", "Support (labelled)", "Predicted", "TP", "FP", "FN", "Precision", "Recall", "F1"],
        rows,
    )


def _confusion_table(res: CategoryResult, classes: tuple[str, ...]) -> str:
    rows = [[f"**{e}**", *[res.confusion[e][p] for p in classes]] for e in classes]
    return _table(["Label \\ Rule output", *classes], rows)


def render_markdown(r: EvaluationResult) -> str:
    t = r.thresholds
    md = r.metadata
    rs, sd = r.categories[Category.RIGHTSIZING], r.categories[Category.SCHEDULED_SHUTDOWN]
    rec = {
        cat: next(m for m in res.metrics if m.label == "RECOMMEND")
        for cat, res in r.categories.items()
    }
    n_miss = sum(1 for m in r.misses if m.category != "evidence_quality")
    unpredicted = [row for row in r.prediction_rows if row["result"] == "NOT PREDICTED"]
    savings_numeric = [s for s in r.savings if s.abs_error_usd is not None]
    max_err = max((s.abs_error_usd for s in savings_numeric), default=None)
    mean_err = (
        sum(s.abs_error_usd for s in savings_numeric) / len(savings_numeric)
        if savings_numeric
        else None
    )
    total_violations = sum(len(v) for _, v in r.safety)
    correct = {
        cat: res.n - sum(1 for m in r.misses if m.category == str(cat))
        for cat, res in r.categories.items()
    }

    out: list[str] = []
    w = out.append
    w("# Evaluation Report")
    w("")
    w("> **SYNTHETIC DATA.** This report validates rule *logic* on synthetic, deliberately")
    w("> constructed scenarios. It is **not** a measure of real-world accuracy. Prices are")
    w("> illustrative, not current AWS pricing. All savings are *estimated*, none *realized*.")
    w("")
    w("Generated by `python tasks.py evaluate` (`python -m src.evaluation.report`).")
    w("")
    w(
        _table(
            ["Item", "Value"],
            [
                [
                    "Dataset",
                    f"{md.get('resource_count')} synthetic resources, seed "
                    f"{md.get('seed')}, generator v{md.get('generator_version')}",
                ],
                ["Loaded / rejected", f"{r.n_resources} / {r.n_rejected}"],
                [
                    "Labelled decisions",
                    f"{r.n_resources} resources x 2 rules = {2 * r.n_resources}",
                ],
                ["Analysis time (as_of)", t.analysis.as_of.isoformat()],
                [
                    "Rightsizing rule",
                    f"mean < {t.rightsizing.max_mean_cpu_pct:g}% AND p95 < "
                    f"{t.rightsizing.max_p95_cpu_pct:g}%",
                ],
                [
                    "Data-quality gate",
                    f">= {t.data_quality.min_coverage_days:g} days, >= "
                    f"{t.data_quality.min_completeness:.0%} complete, <= "
                    f"{t.data_quality.max_staleness_hours:g} h stale",
                ],
                ["Labels", "written from scenario definitions and committed before the rules"],
                ["Threshold changes after seeing results", "none"],
            ],
        )
    )
    w("")

    w("## 1. Headline results")
    w("")
    w(
        _table(
            [
                "Rule",
                "Accuracy (all 4 outcomes)",
                "RECOMMEND precision",
                "RECOMMEND recall",
                "RECOMMEND F1",
            ],
            [
                [
                    "Rightsizing",
                    f"{_f(rs.accuracy)} ({correct[Category.RIGHTSIZING]}/{rs.n})",
                    _f(rec[Category.RIGHTSIZING].precision),
                    _f(rec[Category.RIGHTSIZING].recall),
                    _f(rec[Category.RIGHTSIZING].f1),
                ],
                [
                    "Scheduled shutdown",
                    f"{_f(sd.accuracy)} ({correct[Category.SCHEDULED_SHUTDOWN]}/{sd.n})",
                    _f(rec[Category.SCHEDULED_SHUTDOWN].precision),
                    _f(rec[Category.SCHEDULED_SHUTDOWN].recall),
                    _f(rec[Category.SCHEDULED_SHUTDOWN].f1),
                ],
            ],
        )
    )
    w("")
    w(f"- Outcome misses: **{n_miss}** of {2 * r.n_resources} labelled decisions.")
    w(f"- Evidence-quality accuracy: **{_f(r.quality.accuracy)}**.")
    w(
        f"- Savings checks passed: **{sum(s.passed for s in r.savings)}/{len(r.savings)}** "
        f"(max absolute error {_f(max_err, 4)} USD/month)."
    )
    w(f"- Safety-invariant violations: **{total_violations}**.")
    w(f"- Misses not pre-registered: **{len(unpredicted)}**.")
    w("")
    w("### By subset")
    w("")
    w("Non-ambiguous scenarios mostly check that the code implements its own specification;")
    w("the deliberately ambiguous/boundary scenarios are the informative part.")
    w("")
    subset_rows = []
    for name, amb, n_res in [
        ("Non-ambiguous", False, r.n_resources - r.n_ambiguous_resources),
        ("Ambiguous / boundary", True, r.n_ambiguous_resources),
    ]:
        n_dec = 2 * n_res
        wrong = sum(1 for m in r.misses if m.category != "evidence_quality" and m.ambiguous == amb)
        subset_rows.append(
            [name, n_res, n_dec, n_dec - wrong, wrong, _f(safe_div(n_dec - wrong, n_dec))]
        )
    w(_table(["Subset", "Resources", "Decisions", "Correct", "Misses", "Accuracy"], subset_rows))
    w("")
    w("`n/a` means the metric is undefined (zero denominator): precision is undefined when")
    w("the rules never output that class; recall when the class never occurs in the labels.")
    w("")

    w("## 2. Pre-registered predictions")
    w("")
    w("Committed in `data/labels/preregistered_predictions.yaml` **before** the evaluation code")
    w("existed. Prior knowledge at the time is disclosed in that file.")
    w("")
    w(
        _table(
            ["ID", "Resource", "Scenario", "Category", "Label", "Predicted output", "Reasoning"],
            [
                [
                    p["id"],
                    p["resource_id"],
                    p["scenario"],
                    p["category"],
                    p["expected_label"],
                    p["predicted_rule_output"],
                    p["reasoning"],
                ]
                for p in r.predictions["predicted_misses"]
            ],
        )
    )
    w("")
    other = r.predictions.get("other_predictions", {})
    for k, v in other.items():
        w(f"- **{k}**: {v}")
    w("")

    w("## 3. Predicted vs actual misses")
    w("")
    w(
        _table(
            ["ID", "Resource", "Scenario", "Category", "Label", "Predicted", "Actual", "Result"],
            [
                [
                    x["id"],
                    x["resource_id"],
                    x["scenario"],
                    x["category"],
                    x["label"],
                    x["predicted"],
                    x["actual"],
                    f"**{x['result']}**",
                ]
                for x in r.prediction_rows
            ],
        )
    )
    w("")

    for cat, title in [
        (Category.RIGHTSIZING, "Rightsizing"),
        (Category.SCHEDULED_SHUTDOWN, "Scheduled shutdown"),
    ]:
        res = r.categories[cat]
        w(f"## {4 if cat == Category.RIGHTSIZING else 5}. {title}")
        w("")
        w("Per-class (one-vs-rest) metrics:")
        w("")
        w(_metrics_table(res))
        w("")
        w("Confusion matrix (rows = ground-truth label, columns = rule output):")
        w("")
        w(_confusion_table(res, OUTCOMES))
        w("")

    w("## 6. Evidence quality")
    w("")
    w(_metrics_table(r.quality))
    w("")
    w(_confusion_table(r.quality, QUALITIES))
    w("")

    w("## 7. Savings calculation error")
    w("")
    w("Expected values were computed by hand (`data/labels/expected_savings.csv`, formula in")
    w(f"each row), independently of the engine. Tolerance: {SAVINGS_TOLERANCE_USD} USD. Percentage")
    w("error is shown only when the true value is at least 1 USD (it is meaningless near 0).")
    w("")
    w(
        _table(
            [
                "Resource",
                "Category",
                "Expected USD/mo",
                "Engine USD/mo",
                "Abs error",
                "% error",
                "Pass",
                "Note",
            ],
            [
                [
                    s.resource_id,
                    s.category,
                    _f(s.expected_usd, 4),
                    _f(s.actual_usd, 4),
                    _f(s.abs_error_usd, 6),
                    _f(s.pct_error, 6),
                    "yes" if s.passed else "**NO**",
                    s.note,
                ]
                for s in r.savings
            ],
        )
    )
    w("")
    w(
        f"Mean absolute error over {len(savings_numeric)} numeric cases: {_f(mean_err, 6)} USD; "
        f"max: {_f(max_err, 6)} USD."
    )
    w("")

    w("## 8. Safety invariants")
    w("")
    w("Also enforced as tests in `tests/test_safety.py`.")
    w("")
    w(
        _table(
            ["Invariant", "Violations", "Offending assessments"],
            [[name, len(v), ", ".join(v) or "-"] for name, v in r.safety],
        )
    )
    w("")

    w("## 9. Every miss, with evidence")
    w("")
    if not r.misses:
        w("No misses.")
    else:
        rows = []
        for m in r.misses:
            ev = m.assessment.evidence
            rows.append(
                [
                    m.resource_id,
                    m.scenario,
                    m.category,
                    m.expected,
                    m.actual,
                    "yes" if m.ambiguous else "no",
                    _f(ev.mean_cpu_pct, 1),
                    _f(ev.p95_cpu_pct, 1),
                    _f(ev.max_cpu_pct, 1),
                    _f(ev.off_hours_mean_cpu_pct, 1),
                    f"{ev.completeness:.1%}",
                    f"{ev.coverage_days:g}",
                    _f(ev.staleness_hours, 0),
                    m.assessment.risk_level or "-",
                ]
            )
        w(
            _table(
                [
                    "Resource",
                    "Scenario",
                    "Category",
                    "Label",
                    "Rule output",
                    "Ambiguous",
                    "Mean %",
                    "p95 %",
                    "Max %",
                    "Off-hours mean %",
                    "Complete",
                    "Days",
                    "Stale h",
                    "Risk",
                ],
                rows,
            )
        )
    w("")
    w("Human analysis of each miss and any proposed rule changes: `docs/EVALUATION.md`.")
    w("")

    w("## 10. Limitations")
    w("")
    w("- Scenarios and labels were written by the same author who wrote the rules, so the")
    w("  non-ambiguous cases mostly confirm that the code implements its own specification.")
    w("  The ambiguous cases are the informative part.")
    w("- 88 resources and 26 scenarios is a small, hand-designed set; per-class metrics on")
    w("  small classes (e.g. 2 NEEDS_HUMAN_REVIEW shutdown labels) are very noisy.")
    w("- CPU only: memory, disk and network effects of downsizing are not evaluated.")
    w("- Savings checks validate arithmetic against hand calculations, not real bills.")
    w("- No real AWS data was used. Real-world precision/recall is unknown.")
    w("")
    return "\n".join(out)


def write_report(path: Path = REPORT_PATH) -> EvaluationResult:
    result = run_evaluation()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(render_markdown(result))
    return result


if __name__ == "__main__":
    res = write_report()
    n_miss = sum(1 for m in res.misses if m.category != "evidence_quality")
    print(
        f"Wrote {REPORT_PATH} | outcome misses: {n_miss} | "
        f"safety violations: {sum(len(v) for _, v in res.safety)} (SYNTHETIC)"
    )
