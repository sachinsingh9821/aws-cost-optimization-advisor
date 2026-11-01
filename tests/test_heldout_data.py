"""Held-out dataset and labels integrity."""

from __future__ import annotations

from src.data.generator import HELDOUT_OUT_DIR, HELDOUT_SCENARIOS, HELDOUT_SEED, write_dataset
from src.data.loader import load_resources
from src.evaluation.labels import (
    HELDOUT_LABELS_PATH,
    HELDOUT_MANIFEST_PATH,
    HELDOUT_SCENARIO_LABELS,
    OUTCOMES,
    SCENARIO_LABELS,
    build_ground_truth,
    read_ground_truth,
    read_manifest,
)


def test_heldout_dataset_reproducible(tmp_path):
    data, manifest = write_dataset(tmp_path, HELDOUT_SEED, HELDOUT_SCENARIOS, "ho-")
    assert data.read_bytes() == (HELDOUT_OUT_DIR / "resources.json").read_bytes()
    assert manifest.read_bytes() == HELDOUT_MANIFEST_PATH.read_bytes()


def test_heldout_loads_and_is_disjoint_from_original():
    held = load_resources(HELDOUT_OUT_DIR / "resources.json")
    assert held.rejected == []
    assert all(r.resource_id.startswith("ho-") for r in held.resources)
    original_ids = set(read_manifest())
    assert not original_ids & {r.resource_id for r in held.resources}


def test_heldout_labels_complete_and_in_sync():
    assert set(HELDOUT_SCENARIO_LABELS) == {s.name for s in HELDOUT_SCENARIOS}
    assert not set(HELDOUT_SCENARIO_LABELS) & set(SCENARIO_LABELS)
    for lab in HELDOUT_SCENARIO_LABELS.values():
        assert lab.rightsizing in OUTCOMES and lab.shutdown in OUTCOMES
    expected = build_ground_truth(read_manifest(HELDOUT_MANIFEST_PATH), HELDOUT_SCENARIO_LABELS)
    assert read_ground_truth(HELDOUT_LABELS_PATH) == expected


def test_heldout_has_should_and_should_not_catch_cases():
    labels = HELDOUT_SCENARIO_LABELS.values()
    assert sum(lab.rightsizing == "NEEDS_HUMAN_REVIEW" for lab in labels) >= 3
    assert sum(lab.rightsizing == "RECOMMEND" for lab in labels) >= 3
    assert sum(lab.shutdown == "NEEDS_HUMAN_REVIEW" for lab in labels) >= 3
    assert sum(lab.shutdown == "RECOMMEND" for lab in labels) >= 3
