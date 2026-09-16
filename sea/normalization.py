"""Normalization strategies applied to an aligned SEA event before
aggregation. Three interchangeable conventions (space-weather-platform-todo.md
section 5): raw values, deviation from each event's own pre-event baseline
(the default), and normalized amplitude (deviation scaled by the baseline's
own spread). All three share the same signature so a caller can swap modes
without touching alignment or aggregation code.

Baseline-deviation is the default because it's the standard technique in the
SEA literature: it isolates the storm-driven change from each event's own
ambient/solar-cycle-phase level, while keeping results in physical units
(nT, km/s) that are directly interpretable. Raw values remain appropriate
for indices like Dst that are already defined as a deviation from a
quiet-time reference. Normalized amplitude sacrifices physical units in
exchange for cross-parameter and cross-event comparability.
"""

import statistics
from dataclasses import replace

from alignment import AlignedEvent

DEFAULT_BASELINE_START_OFFSET = -48
DEFAULT_BASELINE_END_OFFSET = -6


def _baseline_values(offsets: list, values: list, baseline_start_offset: int, baseline_end_offset: int) -> list:
    return [
        value
        for offset, value in zip(offsets, values)
        if baseline_start_offset <= offset <= baseline_end_offset and value is not None
    ]


def normalize_raw(offsets: list, aligned_event: AlignedEvent, **kwargs) -> AlignedEvent:
    """No-op: returns the event unchanged. `**kwargs` absorbs the baseline
    window arguments the other strategies take, so all three share one
    call signature."""
    return aligned_event


def normalize_baseline_deviation(
    offsets: list,
    aligned_event: AlignedEvent,
    baseline_start_offset: int = DEFAULT_BASELINE_START_OFFSET,
    baseline_end_offset: int = DEFAULT_BASELINE_END_OFFSET,
) -> AlignedEvent:
    baseline = _baseline_values(offsets, aligned_event.values, baseline_start_offset, baseline_end_offset)
    if not baseline:
        # No usable baseline for this event -- normalizing would be
        # meaningless, so this event contributes nothing rather than
        # silently mixing raw and normalized conventions in one aggregate.
        return replace(aligned_event, values=[None] * len(aligned_event.values))

    baseline_mean = statistics.mean(baseline)
    normalized = [None if value is None else value - baseline_mean for value in aligned_event.values]
    return replace(aligned_event, values=normalized)


def normalize_amplitude(
    offsets: list,
    aligned_event: AlignedEvent,
    baseline_start_offset: int = DEFAULT_BASELINE_START_OFFSET,
    baseline_end_offset: int = DEFAULT_BASELINE_END_OFFSET,
) -> AlignedEvent:
    baseline = _baseline_values(offsets, aligned_event.values, baseline_start_offset, baseline_end_offset)
    if not baseline:
        return replace(aligned_event, values=[None] * len(aligned_event.values))

    baseline_mean = statistics.mean(baseline)
    baseline_spread = statistics.pstdev(baseline)
    if baseline_spread == 0:
        # A perfectly flat baseline can't produce a meaningful z-score.
        return replace(aligned_event, values=[None] * len(aligned_event.values))

    normalized = [None if value is None else (value - baseline_mean) / baseline_spread for value in aligned_event.values]
    return replace(aligned_event, values=normalized)


STRATEGIES = {
    "raw": normalize_raw,
    "baseline_deviation": normalize_baseline_deviation,
    "normalized_amplitude": normalize_amplitude,
}
