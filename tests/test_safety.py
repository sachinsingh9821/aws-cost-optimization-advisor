"""Safety invariants, checked on crafted inputs AND on the full synthetic dataset.

These must hold regardless of thresholds; a failure here is a release blocker.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from src.data.loader import load_resources
from src.evaluation.labels import read_manifest
from src.rules.engine import assess_all
from src.schema import Category, Criticality, EvidenceQuality, Outcome, Resource
from tests.conftest import AS_OF, make_raw


@pytest.fixture(scope="module")
def dataset_assessments(thresholds, pricing):
    resources = load_resources().resources
    return resources, assess_all(resources, thresholds, pricing)


def test_critical_never_auto_flagged_for_shutdown(dataset_assessments, thresholds, pricing):
    crafted = Resource.model_validate(
        make_raw(criticality="critical", environment="dev", schedule_eligible=True)
    )
    resources, assessments = dataset_assessments
    assessments = assessments + assess_all([crafted], thresholds, pricing)
    critical = {
        r.resource_id for r in [*resources, crafted] if r.criticality == Criticality.CRITICAL
    }
    for a in assessments:
        if a.resource_id in critical:
            assert a.outcome != Outcome.RECOMMEND, a.id
            if a.is_recommendation:
                assert a.human_review_required


def test_stale_data_never_high_evidence(dataset_assessments, thresholds):
    resources, assessments = dataset_assessments
    stale = {
        r.resource_id
        for r in resources
        if r.last_observed_at is None
        or (thresholds.analysis.as_of - r.last_observed_at)
        > timedelta(hours=thresholds.evidence_quality.high_max_staleness_hours)
    }
    assert stale  # the dataset must actually contain stale cases
    for a in assessments:
        if a.resource_id in stale:
            assert a.evidence_quality != EvidenceQuality.HIGH, a.id


def test_unknown_price_never_yields_savings_number(dataset_assessments):
    resources, assessments = dataset_assessments
    unpriced = {r.resource_id for r in resources if r.unit_price_hourly is None}
    assert unpriced
    for a in assessments:
        if a.resource_id in unpriced and a.savings is not None:
            assert a.savings.monthly_usd is None
            assert a.savings.cannot_estimate_reason


def test_bursty_resources_not_flagged_for_rightsizing(dataset_assessments):
    _, assessments = dataset_assessments
    bursty = {rid for rid, sc in read_manifest().items() if sc == "bursty"}
    assert bursty
    for a in assessments:
        if a.resource_id in bursty and a.category == Category.RIGHTSIZING:
            assert a.outcome == Outcome.NO_ACTION, a.id


def test_low_evidence_never_actionable(dataset_assessments):
    _, assessments = dataset_assessments
    for a in assessments:
        if a.evidence_quality == EvidenceQuality.LOW:
            assert a.outcome == Outcome.INSUFFICIENT_DATA, a.id


def test_prod_never_scheduled_off(dataset_assessments):
    resources, assessments = dataset_assessments
    prod = {r.resource_id for r in resources if r.environment == "prod"}
    for a in assessments:
        if a.resource_id in prod and a.category == Category.SCHEDULED_SHUTDOWN:
            assert not a.is_recommendation, a.id


def test_untagged_never_scheduled_off(dataset_assessments):
    resources, assessments = dataset_assessments
    untagged = {r.resource_id for r in resources if not r.schedule_eligible}
    for a in assessments:
        if a.resource_id in untagged and a.category == Category.SCHEDULED_SHUTDOWN:
            assert not a.is_recommendation, a.id


def test_all_savings_are_labelled_estimated(dataset_assessments):
    _, assessments = dataset_assessments
    for a in assessments:
        if a.savings is not None:
            assert a.savings.kind == "estimated"
            assert any("ESTIMATED" in s for s in a.assumptions)


def test_missing_metrics_never_treated_as_idle(thresholds, pricing):
    """A resource with no metrics must not look 'idle' (0% CPU) and get downsized."""
    r = Resource.model_validate(
        make_raw(cpu_hourly=None, last_observed_at=(AS_OF - timedelta(hours=1)).isoformat())
    )
    for a in assess_all([r], thresholds, pricing):
        assert a.outcome == Outcome.INSUFFICIENT_DATA


def test_critical_ordering_on_dataset(dataset_assessments):
    """Critical + low usage -> review; critical + healthy usage -> no action (not noise)."""
    _, assessments = dataset_assessments
    manifest = read_manifest()
    by_scenario: dict[str, set[Outcome]] = {}
    for a in assessments:
        if a.category == Category.RIGHTSIZING:
            by_scenario.setdefault(manifest[a.resource_id], set()).add(a.outcome)
    assert by_scenario["critical_underutilized"] == {Outcome.NEEDS_HUMAN_REVIEW}
    assert by_scenario["critical_normal"] == {Outcome.NO_ACTION}
