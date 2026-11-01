"""Schema validation, loader behaviour, and generator determinism."""

from __future__ import annotations

import csv
import json

import pytest
from pydantic import ValidationError

from src.data.generator import SCENARIOS, generate, write_dataset
from src.data.loader import DEFAULT_PATH, load_resources, validate_records
from src.schema import Resource
from tests.conftest import make_raw

# --- Schema: valid and invalid inputs ---------------------------------------


def test_valid_resource_parses():
    r = Resource.model_validate(make_raw())
    assert r.window_hours == 720


def test_missing_cpu_points_stay_none_not_zero():
    cpu = [5.0] * 720
    cpu[10] = None
    r = Resource.model_validate(make_raw(cpu=cpu))
    assert r.cpu_hourly is not None
    assert r.cpu_hourly[10] is None


def test_null_price_allowed_and_kept_null():
    r = Resource.model_validate(make_raw(unit_price_hourly=None))
    assert r.unit_price_hourly is None


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"cpu": [150.0] * 720}, "outside 0-100"),
        ({"cpu": [-1.0] * 720}, "outside 0-100"),
        ({"cpu": [5.0] * 100}, "expected 720"),
        ({"unit_price_hourly": 0}, "greater than 0"),
        ({"unit_price_hourly": -0.1}, "greater than 0"),
        ({"running_hours": -1}, "greater than or equal to 0"),
        ({"running_hours": 10_000}, "exceeds window"),
        ({"environment": "staging"}, "environment"),
        ({"provenance": "aws"}, "provenance"),
        ({"observed_to": "2026-09-01T00:00:00Z"}, "after observed_from"),
        ({"last_observed_at": "2027-01-01T00:00:00Z"}, "inside the observation window"),
        ({"observed_from": "2026-10-01T00:00:00"}, "timezone"),
        ({"unexpected_field": 1}, "Extra inputs"),
    ],
)
def test_invalid_values_rejected(overrides, fragment):
    with pytest.raises(ValidationError, match=fragment):
        Resource.model_validate(make_raw(**overrides))


def test_window_must_be_whole_hours():
    raw = make_raw(cpu_hourly=None)
    raw["observed_from"] = "2026-10-01T00:30:00Z"
    with pytest.raises(ValidationError, match="whole number of hours"):
        Resource.model_validate(raw)


# --- Loader ------------------------------------------------------------------


def test_duplicates_rejected_first_wins():
    a = make_raw(resource_id="dup", region="us-east-1")
    b = make_raw(resource_id="dup", region="eu-west-1")
    resources, rejected = validate_records([a, b])
    assert [r.region for r in resources] == ["us-east-1"]
    assert rejected[0].reason == "duplicate resource_id"
    assert rejected[0].index == 1


def test_invalid_record_rejected_with_reason_others_kept():
    good = make_raw(resource_id="good")
    bad = make_raw(resource_id="bad", cpu=[999.0] * 720)
    resources, rejected = validate_records([good, bad, "not a dict"])
    assert [r.resource_id for r in resources] == ["good"]
    assert rejected[0].resource_id == "bad"
    assert "outside 0-100" in rejected[0].reason
    assert rejected[1].resource_id is None


def test_loader_refuses_unlabelled_data(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"metadata": {}, "resources": []}))
    with pytest.raises(ValueError, match="not labelled synthetic"):
        load_resources(p)


def test_committed_dataset_loads_cleanly():
    result = load_resources(DEFAULT_PATH)
    assert result.metadata["synthetic"] is True
    assert "illustrative" in result.metadata["pricing_label"]
    assert result.rejected == []
    assert 60 <= len(result.resources) <= 100
    assert all(r.provenance == "synthetic" for r in result.resources)


# --- Generator ---------------------------------------------------------------


def test_generator_is_deterministic():
    assert generate(seed=42) == generate(seed=42)


def test_different_seed_changes_data():
    assert generate(seed=1)[0] != generate(seed=2)[0]


def test_committed_dataset_matches_generator(tmp_path):
    """The committed file must be exactly what the seeded generator produces."""
    data_path, manifest_path = write_dataset(tmp_path)
    assert data_path.read_bytes() == DEFAULT_PATH.read_bytes()
    committed_manifest = DEFAULT_PATH.parent / "scenario_manifest.csv"
    assert manifest_path.read_bytes() == committed_manifest.read_bytes()


def test_manifest_covers_every_scenario_and_resource():
    records, manifest = generate()
    assert {rid for rid, _ in manifest} == {r["resource_id"] for r in records}
    counts: dict[str, int] = {}
    for _, sc in manifest:
        counts[sc] = counts.get(sc, 0) + 1
    assert counts == {s.name: s.count for s in SCENARIOS}


def test_resource_data_does_not_leak_scenario_names():
    raw = DEFAULT_PATH.read_text(encoding="utf-8")
    with open(DEFAULT_PATH.parent / "scenario_manifest.csv", encoding="utf-8") as f:
        scenarios = {row["scenario"] for row in csv.DictReader(f)}
    assert not any(sc in raw for sc in scenarios)
