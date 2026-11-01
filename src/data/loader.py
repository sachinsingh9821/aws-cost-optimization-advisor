"""Load and validate resource records.

Invalid records and duplicate IDs are rejected with a reason rather than repaired, so
the caller can show exactly what was excluded. Nothing is silently dropped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from src.schema import Resource

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "synthetic" / "resources.json"


@dataclass(frozen=True)
class Rejection:
    index: int
    resource_id: str | None
    reason: str


@dataclass(frozen=True)
class LoadResult:
    resources: list[Resource]
    rejected: list[Rejection] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def validate_records(records: list[dict[str, Any]]) -> tuple[list[Resource], list[Rejection]]:
    """Validate raw dicts. First occurrence of an ID wins; later duplicates are rejected."""
    resources: list[Resource] = []
    rejected: list[Rejection] = []
    seen: set[str] = set()
    for i, raw in enumerate(records):
        rid = raw.get("resource_id") if isinstance(raw, dict) else None
        try:
            res = Resource.model_validate(raw)
        except ValidationError as exc:
            msgs = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'record'}: {e['msg']}"
                for e in exc.errors()
            )
            rejected.append(Rejection(i, rid, msgs))
            continue
        if res.resource_id in seen:
            rejected.append(Rejection(i, res.resource_id, "duplicate resource_id"))
            continue
        seen.add(res.resource_id)
        resources.append(res)
    return resources, rejected


def load_resources(path: Path = DEFAULT_PATH) -> LoadResult:
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    metadata = payload.get("metadata", {})
    if metadata.get("synthetic") is not True:
        # MVP only accepts clearly labelled synthetic data.
        raise ValueError(f"{path} is not labelled synthetic; refusing to load in MVP")
    resources, rejected = validate_records(payload.get("resources", []))
    return LoadResult(resources, rejected, metadata)
