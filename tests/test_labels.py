"""Ground-truth label integrity (labels are inputs to evaluation, so they are tested too)."""

from __future__ import annotations

from src.data.generator import SCENARIOS
from src.evaluation.labels import (
    OUTCOMES,
    QUALITIES,
    SCENARIO_LABELS,
    build_ground_truth,
    read_expected_savings,
    read_ground_truth,
    read_manifest,
)


def test_every_scenario_has_exactly_one_label():
    assert set(SCENARIO_LABELS) == {s.name for s in SCENARIOS}


def test_label_values_are_valid():
    for lab in SCENARIO_LABELS.values():
        assert lab.rightsizing in OUTCOMES
        assert lab.shutdown in OUTCOMES
        assert lab.evidence_quality in QUALITIES
        assert lab.rationale


def test_ambiguous_cases_are_flagged_and_explained():
    amb = {k: v for k, v in SCENARIO_LABELS.items() if k.startswith("amb_")}
    assert len(amb) >= 5
    assert all(v.ambiguous for v in amb.values())
    assert all(("AMBIGUOUS" in v.rationale) or ("BOUNDARY" in v.rationale) for v in amb.values())


def test_committed_labels_match_definitions():
    assert read_ground_truth() == build_ground_truth(read_manifest())


def test_expected_savings_reference_known_resources():
    ids = set(read_manifest())
    rows = read_expected_savings()
    assert rows
    for row in rows:
        assert row["resource_id"] in ids
        assert row["category"] in {"rightsizing", "shutdown"}
        assert row["hand_calculation"]
