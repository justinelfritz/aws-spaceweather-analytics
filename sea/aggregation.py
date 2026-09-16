"""Aggregate a list of (normalized) aligned events into one epoch-time
profile: mean, median, and percentile bands at each offset, across whatever
events have data there. Events with a None at a given offset are excluded
from that offset's statistics rather than treated as zero.
"""

import math
import statistics
from dataclasses import dataclass

DEFAULT_PERCENTILES = (25, 75)


@dataclass(frozen=True)
class OffsetAggregate:
    offset: int
    n: int  # number of events with non-null data at this offset
    mean: float
    median: float
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
                OffsetAggregate(offset=offset, n=0, mean=None, median=None, percentiles={p: None for p in percentiles})
            )
            continue

        sorted_values = sorted(values)
        results.append(
            OffsetAggregate(
                offset=offset,
                n=len(values),
                mean=statistics.mean(values),
                median=statistics.median(values),
                percentiles={p: _percentile(sorted_values, p) for p in percentiles},
            )
        )
    return results
