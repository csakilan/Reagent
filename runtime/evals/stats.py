"""Statistics for eval reports. SPEC.md §7 "Handling non-determinism".

The platform never claims an improvement that is really noise: a regression is
flagged only when two versions' 95% Wilson intervals do not overlap.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

Z95 = 1.959963984540054


def wilson(passes: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval. With no trials there is no information: (0, 1)."""
    if n <= 0:
        return 0.0, 1.0
    p = passes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return round(max(0.0, centre - half), 6), round(min(1.0, centre + half), 6)


def rate(passes: int, n: int) -> float | None:
    return None if n == 0 else passes / n


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile. 0 for an empty list."""
    if not values:
        return 0.0
    xs = sorted(values)
    k = max(0, min(len(xs) - 1, math.ceil(q / 100 * len(xs)) - 1))
    return xs[k]


def compare(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Version comparison. Overlapping intervals mean 'no significant change'."""
    lo_p, hi_p = previous["pass_rate_ci_low"], previous["pass_rate_ci_high"]
    lo_c, hi_c = current["pass_rate_ci_low"], current["pass_rate_ci_high"]
    if hi_c < lo_p:
        verdict = "regression"
    elif lo_c > hi_p:
        verdict = "improvement"
    else:
        verdict = "no significant change"
    return {"verdict": verdict, "regression": verdict == "regression",
            "previous": [lo_p, hi_p], "current": [lo_c, hi_c]}
