"""Text shared by all rules so assumptions are stated identically everywhere."""

from __future__ import annotations

COST_ASSUMPTIONS = [
    "Compute cost only: excludes storage, data transfer, tax, credits and discounts "
    "(Reserved Instances / Savings Plans).",
    "Prices are illustrative, not current AWS pricing.",
    "Monthly figures use 730 hours per month.",
    "Savings are ESTIMATED from synthetic data; no savings have been realized.",
]

RIGHTSIZING_ASSUMPTIONS = [
    *COST_ASSUMPTIONS,
    "One size down in the same family halves vCPUs, so CPU % roughly doubles.",
    "Future load resembles the observed window.",
]

SHUTDOWN_ASSUMPTIONS = [
    *COST_ASSUMPTIONS,
    "Schedule: on Mon-Fri 09:00-19:00 UTC (50 h/week), stopped otherwise.",
    "Stopped instances incur no compute charge (attached storage still bills; excluded).",
]

CPU_ONLY_MISSING = "Memory, disk and network-latency metrics are not available; CPU only."
