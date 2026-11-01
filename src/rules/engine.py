"""Run the data-quality gate and every rule over a set of resources."""

from __future__ import annotations

from collections.abc import Iterable

from src.config import Pricing, Thresholds
from src.rules.evidence import compute_evidence
from src.rules.quality_gate import check_data_quality
from src.rules.rightsizing import assess_rightsizing
from src.rules.shutdown import assess_shutdown
from src.schema import Assessment, Resource


def assess_resource(
    resource: Resource, thresholds: Thresholds, pricing: Pricing
) -> list[Assessment]:
    ev = compute_evidence(resource, thresholds)
    gate = check_data_quality(ev, thresholds)  # always first
    return [
        assess_rightsizing(resource, ev, gate, thresholds, pricing),
        assess_shutdown(resource, ev, gate, thresholds),
    ]


def assess_all(
    resources: Iterable[Resource], thresholds: Thresholds, pricing: Pricing
) -> list[Assessment]:
    resources = list(resources)
    ids = [r.resource_id for r in resources]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate resource_id in input; deduplicate via the loader first")
    return [a for r in resources for a in assess_resource(r, thresholds, pricing)]
