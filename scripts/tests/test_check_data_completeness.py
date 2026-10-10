import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from check_data_completeness import find_gaps, is_relevant  # noqa: E402


def _ts(*minutes_offsets):
    base = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)
    return [base + timedelta(minutes=m) for m in minutes_offsets]


def test_find_gaps_none_when_evenly_spaced():
    timestamps = _ts(0, 1, 2, 3, 4)
    assert find_gaps(timestamps, expected_interval_seconds=60, tolerance=1.5) == []


def test_find_gaps_flags_a_dropout():
    timestamps = _ts(0, 1, 2, 20, 21)  # 18-minute hole between minute 2 and 20
    gaps = find_gaps(timestamps, expected_interval_seconds=60, tolerance=1.5)
    assert len(gaps) == 1
    start, end = gaps[0]
    assert (end - start) == timedelta(minutes=18)


def test_find_gaps_tolerates_minor_jitter():
    # 90-second spacing against a 60-second expected interval, well under
    # the default 1.5x tolerance threshold (90s) — should not count as a gap.
    base = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)
    timestamps = [base, base + timedelta(seconds=89)]
    assert find_gaps(timestamps, expected_interval_seconds=60, tolerance=1.5) == []


def test_find_gaps_empty_and_single_input():
    assert find_gaps([], expected_interval_seconds=60, tolerance=1.5) == []
    assert find_gaps(_ts(0), expected_interval_seconds=60, tolerance=1.5) == []


def test_is_relevant_defaults_true_when_no_active_field():
    assert is_relevant({"time_tag": "2026-09-15T00:00:00"}) is True


def test_is_relevant_filters_inactive_rtsw_sources():
    assert is_relevant({"time_tag": "2026-09-15T00:00:00", "active": False}) is False
    assert is_relevant({"time_tag": "2026-09-15T00:00:00", "active": True}) is True
