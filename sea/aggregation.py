"""Aggregate a list of (normalized) aligned events into one epoch-time
profile: mean, median, percentile bands, and the mean's standard error/95% CI
at each offset, across whatever events have data there. Events with a None
at a given offset are excluded from that offset's statistics rather than
treated as zero.
"""

import math
import statistics
from dataclasses import dataclass

# 10/90 give a wider envelope than 25/75 for eyeballing tail behavior, without
# the "is this deviation real" claim a confidence interval makes -- see
# ci95_lower/upper below for that.
DEFAULT_PERCENTILES = (10, 25, 75, 90)

# 1.96 is the standard normal z-score for a 95% CI -- not exact for small n
# (a t-score would be), but SEA sample sizes here are large enough, and
# small-n results already carry a separate "small sample" caveat (see
# docs/sea-on-demand-design.md Decision 4, SeaVisualization.jsx's
# SMALL_SAMPLE_THRESHOLD) rather than trying to be precise about it here.
CI95_Z = 1.96


@dataclass(frozen=True)
class OffsetAggregate:
    offset: int
    n: int  # number of events with non-null data at this offset
    mean: float
    median: float
    stderr: float  # standard error of the mean; None when n < 2
    ci95_lower: float  # mean - 1.96 * stderr; None when stderr is None
    ci95_upper: float  # mean + 1.96 * stderr; None when stderr is None
    percentiles: dict  # e.g. {25: ..., 75: ...}


def _percentile(sorted_values: list, pct: float):
    """Linear interpolation between closest ranks -- the same convention
    numpy.percentile uses by default."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * (pct / 100)
    floor_index, ceil_index = math.floor(k), math.ceil(k)
    if floor_index == ceil_index:
        return sorted_values[int(k)]
    lower = sorted_values[floor_index] * (ceil_index - k)
    upper = sorted_values[ceil_index] * (k - floor_index)
    return lower + upper


def aggregate_events(offsets: list, aligned_events: list, percentiles=DEFAULT_PERCENTILES) -> list:
    results = []
    for index, offset in enumerate(offsets):
        values = [event.values[index] for event in aligned_events if event.values[index] is not None]
        if not values:
            results.append(
                OffsetAggregate(
                    offset=offset,
                    n=0,
                    mean=None,
                    median=None,
                    stderr=None,
                    ci95_lower=None,
                    ci95_upper=None,
                    percentiles={p: None for p in percentiles},
                )
            )
            continue

        sorted_values = sorted(values)
        mean = statistics.mean(values)
        stderr = statistics.stdev(values) / math.sqrt(len(values)) if len(values) > 1 else None
        results.append(
            OffsetAggregate(
                offset=offset,
                n=len(values),
                mean=mean,
                median=statistics.median(values),
                stderr=stderr,
                ci95_lower=mean - CI95_Z * stderr if stderr is not None else None,
                ci95_upper=mean + CI95_Z * stderr if stderr is not None else None,
                percentiles={p: _percentile(sorted_values, p) for p in percentiles},
            )
        )
    return results
