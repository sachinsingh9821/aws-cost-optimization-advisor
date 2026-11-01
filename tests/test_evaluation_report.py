"""Evaluation report: reproducibility and predicted-vs-actual matching logic."""

from __future__ import annotations

import pytest

from src.evaluation.report import (
    REPORT_PATH,
    Miss,
    _prediction_rows,
    render_markdown,
    run_evaluation,
)


@pytest.fixture(scope="module")
def result():
    return run_evaluation()


def test_committed_report_matches_fresh_run(result):
    """The committed report must be exactly what the current code and data produce."""
    assert REPORT_PATH.read_text(encoding="utf-8") == render_markdown(result)


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
