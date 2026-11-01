"""Metric functions on small hand-checked toy inputs (no dataset involved)."""

from __future__ import annotations

import pytest

from src.evaluation.metrics import (
    accuracy,
    check_savings,
    class_metrics,
    confusion_matrix,
    f1_score,
    safe_div,
)

CLASSES = ["A", "B", "C"]


def test_class_metrics_hand_checked():
    exp = ["A", "A", "A", "B", "B", "C"]
    pred = ["A", "A", "B", "B", "A", "C"]
    m = {x.label: x for x in class_metrics(exp, pred, CLASSES)}
    # A: TP=2, FP=1 (the B predicted as A), FN=1 -> P=2/3, R=2/3, F1=2/3
    assert (m["A"].tp, m["A"].fp, m["A"].fn) == (2, 1, 1)
    assert m["A"].precision == pytest.approx(2 / 3)
    assert m["A"].recall == pytest.approx(2 / 3)
    assert m["A"].f1 == pytest.approx(2 / 3)
    # B: TP=1, FP=1, FN=1
    assert m["B"].precision == pytest.approx(0.5) and m["B"].recall == pytest.approx(0.5)
    # C: perfect
    assert m["C"].f1 == 1.0
    assert m["A"].support == 3 and m["A"].predicted == 3


def test_zero_denominators_are_undefined_not_zero():
    exp = ["A", "A"]
    pred = ["A", "A"]
    m = {x.label: x for x in class_metrics(exp, pred, CLASSES)}
    assert m["B"].precision is None  # never predicted
    assert m["B"].recall is None  # never occurs
    assert m["B"].f1 is None


def test_never_predicted_but_present():
    m = {x.label: x for x in class_metrics(["B"], ["A"], CLASSES)}
    assert m["B"].recall == 0.0
    assert m["B"].precision is None
    assert m["B"].f1 is None
    assert m["A"].precision == 0.0 and m["A"].recall is None


def test_f1_zero_when_precision_and_recall_zero():
    assert f1_score(0.0, 0.0) == 0.0
    assert f1_score(None, 1.0) is None


def test_confusion_matrix():
    cm = confusion_matrix(["A", "A", "B"], ["A", "B", "B"], CLASSES)
    assert cm["A"] == {"A": 1, "B": 1, "C": 0}
    assert cm["B"]["B"] == 1
    assert sum(sum(r.values()) for r in cm.values()) == 3


def test_confusion_matrix_rejects_unknown_label():
    with pytest.raises(ValueError, match="outside classes"):
        confusion_matrix(["A"], ["Z"], CLASSES)


def test_length_mismatch_rejected():
    with pytest.raises(ValueError, match="same length"):
        class_metrics(["A"], ["A", "B"], CLASSES)


def test_accuracy_and_safe_div():
    assert accuracy(["A", "B"], ["A", "A"]) == 0.5
    assert accuracy([], []) is None
    assert safe_div(1, 0) is None


def test_savings_check_exact_and_error():
    ok = check_savings("r", "rightsizing", 73.0, 73.0)
    assert ok.passed and ok.abs_error_usd == 0.0 and ok.pct_error == 0.0
    bad = check_savings("r", "rightsizing", 73.0, 74.0)
    assert not bad.passed
    assert bad.abs_error_usd == pytest.approx(1.0)
    assert bad.pct_error == pytest.approx(100 / 73)


def test_savings_check_no_pct_error_near_zero():
    c = check_savings("r", "shutdown", 0.10, 0.20)
    assert c.abs_error_usd == pytest.approx(0.10)
    assert c.pct_error is None


@pytest.mark.parametrize(
    ("expected", "actual", "passed"),
    [(None, None, True), (None, 5.0, False), (5.0, None, False)],
)
def test_savings_check_missing_values(expected, actual, passed):
    c = check_savings("r", "shutdown", expected, actual)
    assert c.passed is passed
    assert c.abs_error_usd is None
