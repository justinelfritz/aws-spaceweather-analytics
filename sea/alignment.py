"""Core superposed epoch analysis (SEA) alignment: given a time series and a
list of event times, extract a fixed-width window around each event and
re-index it to hours-relative-to-event (epoch time) so windows from
different events can be compared/aggregated point-by-point.

Deliberately source-agnostic: the series is just {timestamp: value}, and
events are just {id, time} — no dependency on the DONKI catalog or OMNIWeb
schema. The glue that loads those into this shape belongs with the Fargate/
Glue packaging (see space-weather-platform-todo.md section 5), not here.

Real event onset times aren't always hour-aligned (confirmed against the
DONKI catalog: 12 of 200 real events land mid-hour, e.g. ":30"), but the
OMNIWeb series this aligns against is hourly — so event times are rounded
to the nearest hour before indexing. A tie (exactly 30 minutes) rounds to
the nearest *even* hour (Python's default round-half-to-even), which is a
predictable, if arbitrary, choice that doesn't matter once many events are
aggregated.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class Event:
    event_id: str
    time: datetime


@dataclass(frozen=True)
class AlignedEvent:
    event_id: str
    event_time: datetime  # rounded to the nearest hour
    values: list  # one entry per offset in the caller's offsets list, None where missing


def round_to_nearest_hour(dt: datetime) -> datetime:
    seconds = dt.timestamp()
    rounded_seconds = round(seconds / 3600) * 3600
    return datetime.fromtimestamp(rounded_seconds, tz=dt.tzinfo or timezone.utc)


def extract_epoch_window(series: dict, event_time: datetime, hours_before: int, hours_after: int) -> list:
    """Values from `series` at each integer-hour offset from event_time, from
    -hours_before to +hours_after inclusive. A missing key or an explicit
    None in `series` both mean "no data at that offset" and yield None."""
    anchor = round_to_nearest_hour(event_time)
    return [series.get(anchor + timedelta(hours=offset)) for offset in range(-hours_before, hours_after + 1)]


def align_events(series: dict, events: list, hours_before: int, hours_after: int) -> tuple:
    """Returns (offsets, aligned_events) — offsets is the shared list of
    hour offsets every aligned_events[i].values is indexed against."""
    if hours_before < 0 or hours_after < 0:
        raise ValueError("hours_before and hours_after must be >= 0")

    offsets = list(range(-hours_before, hours_after + 1))
    aligned = [
        AlignedEvent(
            event_id=event.event_id,
            event_time=round_to_nearest_hour(event.time),
            values=extract_epoch_window(series, event.time, hours_before, hours_after),
        )
        for event in events
    ]
    return offsets, aligned
