import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aggregation import aggregate_events  # noqa: E402
from alignment import AlignedEvent  # noqa: E402

EVENT_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_event(event_id, values):
    return AlignedEvent(event_id=event_id, event_time=EVENT_TIME, values=values)


def test_aggregate_computes_mean_median_and_percentiles():
    events = [make_event(f"evt-{i}", [v]) for i, v in enumerate([10, 20, 30, 40])]
    [result] = aggregate_events(offsets=[0], aligned_events=events, percentiles=(25, 75))

    assert result.offset == 0
    assert result.n == 4
    assert result.mean == 25
    assert result.median == 25
    assert result.percentiles[25] == 17.5
    assert result.percentiles[75] == 32.5


def test_aggregate_computes_stderr_and_95_percent_ci_of_the_mean():
    # stdev([10, 20, 30, 40]) == 12.909944...; stderr == stdev / sqrt(4)
    events = [make_event(f"evt-{i}", [v]) for i, v in enumerate([10, 20, 30, 40])]
    [result] = aggregate_events(offsets=[0], aligned_events=events, percentiles=(25, 75))

    assert result.stderr == pytest.approx(6.454972243679028)
    assert result.ci95_lower == pytest.approx(result.mean - 1.96 * result.stderr)
    assert result.ci95_upper == pytest.approx(result.mean + 1.96 * result.stderr)


def test_aggregate_stderr_and_ci_are_none_below_two_data_points():
    events = [make_event("evt-0", [10])]
    [result] = aggregate_events(offsets=[0], aligned_events=events, percentiles=(25, 75))

    assert result.n == 1
    assert result.stderr is None
    assert result.ci95_lower is None
    assert result.ci95_upper is None


def test_aggregate_excludes_none_values_from_statistics():
    events = [make_event(f"evt-{i}", [v]) for i, v in enumerate([10, 20, None, 40])]
    [result] = aggregate_events(offsets=[0], aligned_events=events, percentiles=(50,))

    assert result.n == 3
    assert result.mean == 70 / 3
    assert result.median == 20
    assert result.percentiles[50] == 20


def test_aggregate_offset_with_no_data_returns_none_and_zero_count():
    events = [make_event(f"evt-{i}", [None]) for i in range(3)]
    [result] = aggregate_events(offsets=[0], aligned_events=events, percentiles=(25, 75))

    assert result.n == 0
    assert result.mean is None
    assert result.median is None
    assert result.stderr is None
    assert result.ci95_lower is None
    assert result.ci95_upper is None
    assert result.percentiles == {25: None, 75: None}


def test_aggregate_handles_multiple_offsets_independently():
    events = [
        make_event("evt-0", [10, 100]),
        make_event("evt-1", [20, None]),
        make_event("evt-2", [30, 300]),
    ]
    results = aggregate_events(offsets=[-1, 0], aligned_events=events, percentiles=(50,))

    assert results[0].offset == -1
    assert results[0].n == 3
    assert results[0].mean == 20

    assert results[1].offset == 0
    assert results[1].n == 2
    assert results[1].mean == 200
