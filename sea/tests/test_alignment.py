import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alignment import Event, align_events, extract_epoch_window, round_to_nearest_hour  # noqa: E402


def hourly_series(start: datetime, values: list) -> dict:
    return {start + timedelta(hours=i): v for i, v in enumerate(values)}


def test_extract_epoch_window_basic_offsets():
    t0 = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    series = {t0 + timedelta(hours=h): h for h in range(-5, 6)}
    window = extract_epoch_window(series, t0, hours_before=3, hours_after=3)
    assert window == [-3, -2, -1, 0, 1, 2, 3]


def test_extract_epoch_window_missing_hours_yield_none():
    t0 = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    series = {t0 + timedelta(hours=h): h for h in range(-5, 6) if h != -1 and h != 2}
    window = extract_epoch_window(series, t0, hours_before=3, hours_after=3)
    assert window == [-3, -2, None, 0, 1, None, 3]


def test_round_to_nearest_hour_unambiguous_cases():
    assert round_to_nearest_hour(datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)) == datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    assert round_to_nearest_hour(datetime(2026, 1, 1, 12, 29, tzinfo=timezone.utc)) == datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    assert round_to_nearest_hour(datetime(2026, 1, 1, 12, 31, tzinfo=timezone.utc)) == datetime(2026, 1, 1, 13, tzinfo=timezone.utc)


def test_round_to_nearest_hour_exact_tie_picks_one_adjacent_hour():
    # An exact 30-minute tie's rounding direction depends on Python's
    # round-half-to-even applied to the underlying epoch-seconds/3600 value,
    # not on the hour-of-day itself — not worth locking down which way a
    # given tie falls, only that it lands on one of the two real neighbors.
    rounded = round_to_nearest_hour(datetime(2026, 1, 1, 12, 30, tzinfo=timezone.utc))
    assert rounded in (datetime(2026, 1, 1, 12, tzinfo=timezone.utc), datetime(2026, 1, 1, 13, tzinfo=timezone.utc))


def test_align_events_recovers_identical_known_signature_at_different_absolute_times():
    # A distinctive, unmistakable V-shaped "storm" signature: baseline 0 far
    # from onset, dropping to -10 at onset, symmetric recovery either side.
    def signature(offset: int) -> float:
        return -10 + 2 * abs(offset)

    expected = [signature(offset) for offset in range(-5, 6)]

    # Plant the identical relative signature around three different, unrelated
    # absolute event times, each backed by its own series data.
    event_times = [
        datetime(2020, 3, 1, 6, tzinfo=timezone.utc),
        datetime(2021, 11, 17, 18, tzinfo=timezone.utc),
        datetime(2024, 7, 4, 0, tzinfo=timezone.utc),
    ]
    series = {}
    for event_time in event_times:
        for offset in range(-5, 6):
            series[event_time + timedelta(hours=offset)] = signature(offset)

    events = [Event(event_id=f"evt-{i}", time=t) for i, t in enumerate(event_times)]
    offsets, aligned = align_events(series, events, hours_before=5, hours_after=5)

    assert offsets == list(range(-5, 6))
    assert len(aligned) == 3
    for aligned_event in aligned:
        assert aligned_event.values == expected


def test_align_events_rounds_a_mid_hour_event_to_the_nearest_hour():
    anchor_hour = datetime(2011, 2, 4, 20, tzinfo=timezone.utc)  # nearest hour to the real 19:30 DONKI event
    series = hourly_series(anchor_hour - timedelta(hours=2), [10, 20, 30, 40, 50])

    mid_hour_event = Event(event_id="real-2011-02-04", time=datetime(2011, 2, 4, 19, 30, tzinfo=timezone.utc))
    offsets, aligned = align_events(series, [mid_hour_event], hours_before=2, hours_after=2)

    assert aligned[0].event_time == anchor_hour
    assert aligned[0].values == [10, 20, 30, 40, 50]


def test_align_events_rejects_negative_window():
    with pytest.raises(ValueError):
        align_events({}, [], hours_before=-1, hours_after=3)
