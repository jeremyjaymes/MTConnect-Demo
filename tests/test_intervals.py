"""The interval and utilization math: the part that must be right."""
import pytest

from mtc.intervals import compute_intervals, utilization
from mtc.timeutil import parse_ts
from tests.conftest import at


def test_basic_utilization():
    events = [(at(0), "ACTIVE"), (at(60), "READY"), (at(100), "ACTIVE")]
    totals, window = compute_intervals(events, end=at(200))
    assert totals == {"ACTIVE": 160, "READY": 40}
    assert window == 200
    assert utilization(totals, window) == pytest.approx(0.8)


def test_unavailable_is_excluded_from_denominator():
    events = [(at(0), "ACTIVE"), (at(50), "UNAVAILABLE"), (at(150), "READY")]
    totals, window = compute_intervals(events, end=at(200))
    assert totals == {"ACTIVE": 50, "UNAVAILABLE": 100, "READY": 50}
    assert utilization(totals, window) == pytest.approx(0.5)  # 50 / (200 - 100)


def test_restart_marker_is_a_gap_not_active_time():
    events = [(at(0), "ACTIVE"), (at(40), "AGENT_RESTART"), (at(80), "ACTIVE"), (at(100), "READY")]
    totals, window = compute_intervals(events, end=at(200))
    assert totals["AGENT_RESTART"] == 40
    assert totals["ACTIVE"] == 60
    assert utilization(totals, window) == pytest.approx(60 / 160)


def test_window_clipping_uses_state_in_effect_at_start():
    events = [(at(0), "ACTIVE"), (at(100), "READY")]
    totals, window = compute_intervals(events, start=at(50), end=at(150))
    assert totals == {"ACTIVE": 50, "READY": 50}
    assert window == 100


def test_unsorted_input_and_nothing_measurable():
    totals, window = compute_intervals([(at(10), "UNAVAILABLE"), (at(0), "UNAVAILABLE")], end=at(20))
    assert totals == {"UNAVAILABLE": 20}
    assert utilization(totals, window) is None


def test_parse_ts_accepts_extra_fraction_digits_and_assumes_utc():
    assert parse_ts("2026-10-01T18:17:06.148031Z") == parse_ts("2026-10-01T18:17:06.148031123Z")
    assert parse_ts("2026-10-01T18:17:06").tzinfo is not None


def test_data_gap_is_excluded_like_unavailable():
    totals, window = compute_intervals([(at(0), "ACTIVE"), (at(50), "DATA_GAP"), (at(100), "READY")], end=at(150))
    assert utilization(totals, window) == pytest.approx(50 / 100)
