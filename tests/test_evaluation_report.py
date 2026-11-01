"""Evaluation reports: reproducibility, frozen v1 results, and prediction matching."""

from __future__ import annotations

import hashlib

import pytest

from src.evaluation.report import (
    REPORT_PATH,
    REPORT_V1_PATH,
    Miss,
    _outcome_misses,
    _prediction_rows,
    render_markdown,
    render_reports,
    run_all,
)

# SHA-256 of the v1 report as first committed (94a4d48 + subset view, 2754e05).
# v1 results are frozen: no later change may alter them.
V1_REPORT_SHA256 = "aee37867af64b9b7da1d58c61ee8962f10385f010d09f3910fa64be3bcf50066"


@pytest.fixture(scope="module")
def runs():
    return run_all()


@pytest.fixture(scope="module")
def result(runs):
    """v1 on the original set: the run the v1 pre-registered predictions refer to."""
    return runs[("original", "v1")]


def test_committed_reports_match_fresh_run(runs):
    """Both committed reports must be exactly what the current code and data produce."""
    v1_md, v2_md = render_reports(runs)
    assert REPORT_V1_PATH.read_text(encoding="utf-8") == v1_md
    assert REPORT_PATH.read_text(encoding="utf-8") == v2_md


def test_v1_results_are_frozen(runs):
    v1_md, _ = render_reports(runs)
    assert hashlib.sha256(v1_md.encode("utf-8")).hexdigest() == V1_REPORT_SHA256


def test_v2_report_contains_comparison_and_limitation(runs):
    _, v2_md = render_reports(runs)
    assert "## A. Rules v1 vs v2" in v2_md
    assert "same author who" in v2_md
    assert "SYNTHETIC DATA" in v2_md


def test_rule_versions_are_actually_different(runs):
    for ds in ("original", "held-out"):
        v1, v2 = runs[(ds, "v1")], runs[(ds, "v2")]
        assert v1.outputs.keys() == v2.outputs.keys()
    held_v1 = {(m.resource_id, m.category) for m in _outcome_misses(runs[("held-out", "v1")])}
    assert held_v1  # v1 must miss the cases C1/C2 were built for, or the held-out set is moot


def test_report_is_labelled_synthetic(result):
    md = render_markdown(result)
    assert "SYNTHETIC DATA" in md
    assert "not** a measure of real-world accuracy" in md
    assert "illustrative, not current AWS pricing" in md


def test_every_miss_appears_in_predicted_vs_actual(result):
    listed = {(row["resource_id"], row["category"]) for row in result.prediction_rows}
    for m in result.misses:
        assert (m.resource_id, m.category) in listed


def test_savings_and_safety_sections_complete(result):
    assert len(result.savings) == 13
    assert len(result.safety) == 8


# --- _prediction_rows branches, on fabricated misses (no dataset) ------------------


def _miss(rid: str, cat: str, actual: str) -> Miss:
    return Miss(rid, "sc", cat, "NO_ACTION", actual, True, assessment=None)  # type: ignore[arg-type]


PREDS = {
    "predicted_misses": [
        {
            "id": "P1",
            "resource_id": "a",
            "scenario": "sc",
            "category": "rightsizing",
            "expected_label": "NO_ACTION",
            "predicted_rule_output": "RECOMMEND",
        },
        {
            "id": "P2",
            "resource_id": "b",
            "scenario": "sc",
            "category": "shutdown",
            "expected_label": "NO_ACTION",
            "predicted_rule_output": "RECOMMEND",
        },
        {
            "id": "P3",
            "resource_id": "c",
            "scenario": "sc",
            "category": "rightsizing",
            "expected_label": "NO_ACTION",
            "predicted_rule_output": "RECOMMEND",
        },
    ]
}


def test_prediction_rows_all_outcomes():
    misses = [
        _miss("a", "rightsizing", "RECOMMEND"),  # as predicted
        _miss("b", "scheduled_shutdown", "NEEDS_HUMAN_REVIEW"),  # different output
        _miss("z", "rightsizing", "RECOMMEND"),  # not predicted
    ]
    rows = {
        r["id"] if r["id"] != "-" else r["resource_id"]: r for r in _prediction_rows(PREDS, misses)
    }
    assert rows["P1"]["result"] == "Occurred as predicted"
    assert rows["P2"]["result"] == "Occurred, but with a different output"
    assert rows["P3"]["result"] == "Did not occur"
    assert rows["z"]["result"] == "NOT PREDICTED"
