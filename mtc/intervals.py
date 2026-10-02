"""Interval and utilization math. Pure: takes lists of (time, state), returns numbers."""
from datetime import datetime
from typing import Optional, Sequence

from mtc.states import ACTIVE, UNMEASURED
from mtc.timeutil import utc_now

Event = tuple[datetime, str]


def compute_intervals(events: Sequence[Event], start: Optional[datetime] = None,
                      end: Optional[datetime] = None) -> tuple[dict[str, float], float]:
    """Return ({state: seconds}, window_seconds).

    Each state lasts until the next event; the last one lasts until `end` (default: now).
    Intervals are clipped to [start, end]; `start` defaults to the first event.
    """
    ordered = sorted(events, key=lambda event: event[0])
    if not ordered:
        return {}, 0.0
    start = start or ordered[0][0]
    end = end or utc_now()

    seconds_by_state: dict[str, float] = {}
    for index, (began, state) in enumerate(ordered):
        ended = ordered[index + 1][0] if index + 1 < len(ordered) else end
        clipped_start, clipped_end = max(began, start), min(ended, end)
        if clipped_end > clipped_start:
            seconds_by_state[state] = (seconds_by_state.get(state, 0.0)
                                       + (clipped_end - clipped_start).total_seconds())
    return seconds_by_state, max((end - start).total_seconds(), 0.0)


def unmeasured_seconds(seconds_by_state: dict[str, float]) -> float:
    return sum(seconds_by_state.get(state, 0.0) for state in UNMEASURED)


def utilization(seconds_by_state: dict[str, float], window_seconds: float) -> Optional[float]:
    """ACTIVE / (window - unmeasured time). None when nothing is measurable."""
    measured = window_seconds - unmeasured_seconds(seconds_by_state)
    return seconds_by_state.get(ACTIVE, 0.0) / measured if measured > 0 else None
