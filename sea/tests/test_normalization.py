import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alignment import AlignedEvent  # noqa: E402
from normalization import normalize_amplitude, normalize_baseline_deviation, normalize_raw  # noqa: E402

OFFSETS = [-2, -1, 0, 1, 2]
EVENT_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_event(values):
    return AlignedEvent(event_id="evt-1", event_time=EVENT_TIME, values=values)


def test_normalize_raw_is_a_noop():
    event = make_event([100, 200, 50, 10, 20])
    result = normalize_raw(OFFSETS, event, baseline_start_offset=-2, baseline_end_offset=-1)
    assert result.values == [100, 200, 50, 10, 20]


def test_normalize_baseline_deviation_subtracts_baseline_mean():
    # baseline window (offsets -2, -1) = [100, 200], mean = 150
    event = make_event([100, 200, 50, 10, 20])
    result = normalize_baseline_deviation(OFFSETS, event, baseline_start_offset=-2, baseline_end_offset=-1)
    assert result.values == [-50, 50, -100, -140, -130]


def test_normalize_baseline_deviation_ignores_none_within_baseline_window():
    # baseline window = [100, None] -> only 100 counted, mean = 100
    event = make_event([100, None, 50, 10, 20])
    result = normalize_baseline_deviation(OFFSETS, event, baseline_start_offset=-2, baseline_end_offset=-1)
    assert result.values == [0, None, -50, -90, -80]


def test_normalize_baseline_deviation_preserves_none_outside_baseline():
    event = make_event([100, 200, None, 10, None])
    result = normalize_baseline_deviation(OFFSETS, event, baseline_start_offset=-2, baseline_end_offset=-1)
    assert result.values == [-50, 50, None, -140, None]


def test_normalize_baseline_deviation_returns_all_none_when_baseline_fully_missing():
    event = make_event([None, None, 50, 10, 20])
    result = normalize_baseline_deviation(OFFSETS, event, baseline_start_offset=-2, baseline_end_offset=-1)
    assert result.values == [None, None, None, None, None]


def test_normalize_amplitude_divides_deviation_by_baseline_stdev():
    # baseline = [100, 200], mean = 150, population stdev = 50
    event = make_event([100, 200, 50, 10, 20])
    result = normalize_amplitude(OFFSETS, event, baseline_start_offset=-2, baseline_end_offset=-1)
    assert result.values == [-1.0, 1.0, -2.0, -2.8, -2.6]


def test_normalize_amplitude_returns_all_none_when_baseline_is_flat():
    # baseline = [100, 100] -> stdev is 0, can't compute a z-score
    event = make_event([100, 100, 50, 10, 20])
    result = normalize_amplitude(OFFSETS, event, baseline_start_offset=-2, baseline_end_offset=-1)
    assert result.values == [None, None, None, None, None]
