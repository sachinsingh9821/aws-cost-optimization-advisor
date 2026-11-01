"""Order recommendations with a transparent lexicographic sort (no weighted score).

Sort keys, in order:
  1. evidence quality  HIGH before MEDIUM (LOW never reaches here)
  2. risk level        LOW before MEDIUM before HIGH
  3. estimated monthly savings, largest first; "cannot estimate" sorts last
  4. resource_id       deterministic tie-break

Why lexicographic: a weighted score hides trade-offs behind invented weights. Here a
reviewer can always say exactly why item 3 is above item 4.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from src.schema import Assessment, EvidenceQuality, RiskLevel

QUALITY_ORDER = {EvidenceQuality.HIGH: 0, EvidenceQuality.MEDIUM: 1, EvidenceQuality.LOW: 2}
RISK_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}


@dataclass(frozen=True)
class RankedRecommendation:
    rank: int
    assessment: Assessment
    reason: str


def _savings(a: Assessment) -> float | None:
    return a.savings.monthly_usd if a.savings else None


def _key(a: Assessment) -> tuple[int, int, int, float, str]:
    s = _savings(a)
    return (
        QUALITY_ORDER[a.evidence_quality],
        RISK_ORDER[a.risk_level] if a.risk_level else len(RISK_ORDER),
        1 if s is None else 0,
        -(s or 0.0),
        a.id,
    )


def _reason(rank: int, a: Assessment, prev: Assessment | None) -> str:
    s = _savings(a)
    money = f"${s:,.2f}/mo estimated" if s is not None else "savings cannot be estimated"
    risk = a.risk_level.value if a.risk_level else "UNKNOWN"
    text = f"#{rank}: {a.evidence_quality.value} evidence, {risk} risk, {money}"
    if prev is None:
        return text + "."
    if prev.evidence_quality != a.evidence_quality:
        why = "weaker evidence than the item above"
    elif prev.risk_level != a.risk_level:
        why = "same evidence, higher risk than the item above"
    elif _savings(prev) != s:
        why = "same evidence and risk, lower estimated savings"
    else:
        why = "tied on evidence, risk and savings; ordered by ID"
    return f"{text} ({why})."


def rank_recommendations(assessments: Iterable[Assessment]) -> list[RankedRecommendation]:
    recs = sorted((a for a in assessments if a.is_recommendation), key=_key)
    out: list[RankedRecommendation] = []
    for i, a in enumerate(recs, start=1):
        out.append(RankedRecommendation(i, a, _reason(i, a, recs[i - 2] if i > 1 else None)))
    return out
