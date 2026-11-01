"""Evaluation metrics. Pure functions over (expected, predicted) label sequences.

Zero denominators are reported as None ("undefined"), never as 0 or 1:
  precision = TP / (TP + FP)   undefined if the class was never predicted
  recall    = TP / (TP + FN)   undefined if the class never occurs in the labels
  F1        = 2PR / (P + R)    undefined if P or R is undefined; 0 if P + R == 0
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

# Percentage error is only meaningful when the true value is not ~0.
MIN_PCT_ERROR_BASE_USD = 1.0
SAVINGS_TOLERANCE_USD = 0.005  # half a cent


def safe_div(num: float, den: float) -> float | None:
    return None if den == 0 else num / den


@dataclass(frozen=True)
class ClassMetrics:
    label: str
    tp: int
    fp: int
    fn: int
    support: int  # number of times the label occurs in ground truth
    predicted: int  # number of times the rules output this label
    precision: float | None
    recall: float | None
    f1: float | None


def f1_score(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None:
        return None
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def class_metrics(
    expected: Sequence[str], predicted: Sequence[str], classes: Sequence[str]
) -> list[ClassMetrics]:
    """One-vs-rest precision / recall / F1 for each class."""
    if len(expected) != len(predicted):
        raise ValueError("expected and predicted must have the same length")
    out = []
    for c in classes:
        tp = sum(e == c and p == c for e, p in zip(expected, predicted, strict=True))
        fp = sum(e != c and p == c for e, p in zip(expected, predicted, strict=True))
        fn = sum(e == c and p != c for e, p in zip(expected, predicted, strict=True))
        precision, recall = safe_div(tp, tp + fp), safe_div(tp, tp + fn)
        out.append(
            ClassMetrics(
                label=c,
                tp=tp,
                fp=fp,
                fn=fn,
                support=tp + fn,
                predicted=tp + fp,
                precision=precision,
                recall=recall,
                f1=f1_score(precision, recall),
            )
        )
    return out


def confusion_matrix(
    expected: Sequence[str], predicted: Sequence[str], classes: Sequence[str]
) -> dict[str, dict[str, int]]:
    """matrix[expected_label][predicted_label] = count."""
    matrix = {e: dict.fromkeys(classes, 0) for e in classes}
    for e, p in zip(expected, predicted, strict=True):
        if e not in matrix or p not in matrix[e]:
            raise ValueError(f"label outside classes: expected={e!r} predicted={p!r}")
        matrix[e][p] += 1
    return matrix


def accuracy(expected: Sequence[str], predicted: Sequence[str]) -> float | None:
    hits = sum(e == p for e, p in zip(expected, predicted, strict=True))
    return safe_div(hits, len(expected))


@dataclass(frozen=True)
class SavingsCheck:
    resource_id: str
    category: str
    expected_usd: float | None
    actual_usd: float | None
    abs_error_usd: float | None
    pct_error: float | None  # None when the true value is ~0 or either side is missing
    passed: bool
    note: str


def check_savings(
    resource_id: str,
    category: str,
    expected_usd: float | None,
    actual_usd: float | None,
    tolerance: float = SAVINGS_TOLERANCE_USD,
) -> SavingsCheck:
    if expected_usd is None:
        ok = actual_usd is None
        note = "no number expected; none produced" if ok else "number produced but none expected"
        return SavingsCheck(resource_id, category, None, actual_usd, None, None, ok, note)
    if actual_usd is None:
        return SavingsCheck(
            resource_id, category, expected_usd, None, None, None, False, "no estimate produced"
        )
    err = abs(actual_usd - expected_usd)
    pct = 100 * err / abs(expected_usd) if abs(expected_usd) >= MIN_PCT_ERROR_BASE_USD else None
    return SavingsCheck(
        resource_id,
        category,
        expected_usd,
        actual_usd,
        err,
        pct,
        err <= tolerance,
        "within tolerance" if err <= tolerance else f"error exceeds {tolerance} USD",
    )
