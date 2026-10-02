"""End to end against the fake agent: the /current -> /sample pattern, outages, restarts, buffer wraps."""
from datetime import timedelta

import requests

from mtc import collector
from mtc.collector import DevicePoller
from mtc.models import Source
from mtc.intervals import compute_intervals
from mtc.timeutil import format_ts, utc_now
from tests.conftest import at, iso


def poller(store, agent, tracked=("Execution",)):
    return DevicePoller(store, requests.Session(), Source(agent.url, "X"), tracked)


def states(store):
    return [state for _, state in store.execution_events("X")]


def test_changes_between_polls_are_not_missed(agent, store):
    agent.publish("ACTIVE", iso(0))
    p = poller(store, agent)
    p.poll()
    agent.publish("READY", iso(10))   # both of these happen between two polls
    agent.publish("ACTIVE", iso(35))
    p.poll()
    p.poll()  # nothing new: must add nothing

    assert states(store) == ["ACTIVE", "READY", "ACTIVE"]
    totals, _ = compute_intervals(store.execution_events("X"), end=at(60))
    assert totals == {"ACTIVE": 10 + 25, "READY": 25}

    rows = store.db.execute("SELECT state, duration_seconds FROM state_intervals "
                            "WHERE device = 'X' ORDER BY start_ts").fetchall()
    assert [(s, round(d)) for s, d in rows[:2]] == [("ACTIVE", 10), ("READY", 25)]


def test_first_poll_uses_current_then_sample_from_next_sequence_with_item_filter(agent, store):
    agent.publish("ACTIVE", iso(0))
    p = poller(store, agent)
    p.poll()
    p.poll()
    first, second = agent.requests_seen
    assert "/current" in first and "path=" not in first          # learn the items once, unfiltered
    assert "/sample" in second and "from=2" in second            # continue from nextSequence
    assert "%40id%3D%22execution%22" in second                   # only the data items we log


def test_catching_up_in_several_batches(agent, store, monkeypatch):
    monkeypatch.setattr(collector, "SAMPLE_COUNT", 2)
    agent.publish("ACTIVE", iso(0))
    p = poller(store, agent)
    p.poll()
    for i, state in enumerate(["READY", "ACTIVE", "READY", "ACTIVE", "READY"], start=1):
        agent.publish(state, iso(i * 10))
    p.poll()
    assert states(store) == ["ACTIVE", "READY", "ACTIVE", "READY", "ACTIVE", "READY"]


def test_outage_logs_unavailable_once_and_recovery_is_ordered_after_it(agent, store):
    agent.publish("READY", iso(0))   # an old timestamp: the state began long before the outage
    p = poller(store, agent)
    p.poll()
    agent.down = True
    p.poll()
    p.poll()
    agent.down = False
    p.poll()

    events = store.execution_events("X")
    assert [s for _, s in events] == ["READY", "UNAVAILABLE", "READY"]
    assert events == sorted(events, key=lambda e: e[0])           # recovery sorts after the marker
    assert events[2][0] >= events[1][0]


def test_agent_restart_resyncs_and_marks_gap(agent, store):
    agent.publish("ACTIVE", iso(0))
    p = poller(store, agent)
    p.poll()
    agent.restart()
    soon = lambda seconds: format_ts(utc_now() + timedelta(seconds=seconds))  # markers use the local clock
    agent.publish("ACTIVE", soon(0))
    p.poll()
    agent.publish("READY", soon(5))
    p.poll()  # sampling works again on the new instance

    assert states(store) == ["ACTIVE", "AGENT_RESTART", "ACTIVE", "READY"]
    assert store.last_instance_id("X") == "2"


def test_buffer_wrap_records_data_gap_then_resyncs(agent, store):
    agent.publish("ACTIVE", iso(0))
    p = poller(store, agent)
    p.poll()
    agent.publish("READY", iso(10))
    agent.trim()                      # the agent's buffer wrapped past our position
    agent.publish("READY", iso(20))
    p.poll()

    assert states(store) == ["ACTIVE", "DATA_GAP", "READY"]


def test_tracks_extra_items(agent, store):
    agent.publish("ACTIVE", iso(0), program="O1")
    p = poller(store, agent, tracked=("Execution", "Program"))
    p.poll()
    agent.publish("ACTIVE", iso(5), program="O2")
    p.poll()
    assert store.last_values("X")["program"] == ("Program", "O2")


def test_agent_never_reachable_logs_nothing_and_does_not_crash(agent, store):
    agent.down = True
    poller(store, agent).poll()
    assert states(store) == []


# ---------- conditions ----------

CONDITION_TRACKING = ("Execution", "Condition")


def condition_rows(store, device="X"):
    return store.db.execute("SELECT level, native_code, message FROM conditions WHERE device = ? ORDER BY id"
                            .replace("?", store.placeholder), (device,)).fetchall()


def test_fault_is_stored_when_it_starts_and_when_it_clears_and_baseline_normal_is_not(agent, store):
    agent.publish("ACTIVE", iso(0))
    agent.publish_condition("Normal", iso(0), code="")           # baseline every item reports
    p = poller(store, agent, CONDITION_TRACKING)
    p.poll()
    assert condition_rows(store) == []

    agent.publish_condition("Fault", iso(10), message="OUTSIDE INTERLOCK AXIS ( B, ,  )")
    p.poll()
    assert store.active_conditions("X") == {("system_cond", "128"): ("FAULT", "OUTSIDE INTERLOCK AXIS ( B, ,  )", "SYSTEM")}
    agent.publish_condition("Fault", iso(11), message="OUTSIDE INTERLOCK AXIS ( B, ,  )")  # repeat: ignored
    agent.publish_condition("Normal", iso(20))                   # same code clears it
    p.poll()

    assert condition_rows(store) == [("FAULT", "128", "OUTSIDE INTERLOCK AXIS ( B, ,  )"), ("NORMAL", "128", "")]
    assert store.active_conditions("X") == {}
    row = store.db.execute("SELECT level, start_ts, end_ts FROM condition_intervals WHERE device = 'X'").fetchone()
    assert row[0] == "FAULT" and row[2] is not None


def test_two_codes_on_one_item_are_independent_and_codeless_normal_clears_all(agent, store):
    agent.publish("ACTIVE", iso(0))
    agent.publish_condition("Normal", iso(0), code="")
    p = poller(store, agent, CONDITION_TRACKING)
    p.poll()
    agent.publish_condition("Fault", iso(5), code="1", message="A")
    agent.publish_condition("Warning", iso(6), code="2", message="B")
    agent.publish_condition("Normal", iso(7), code="1")          # clears only code 1
    p.poll()
    assert set(store.active_conditions("X")) == {("system_cond", "2")}
    agent.publish_condition("Normal", iso(8), code="")           # no code: clears the rest
    p.poll()
    assert store.active_conditions("X") == {}


def test_fault_cleared_while_we_were_not_watching_is_closed_on_resync(agent, store):
    agent.publish("ACTIVE", iso(0))
    agent.publish_condition("Normal", iso(0), code="")
    p = poller(store, agent, CONDITION_TRACKING)
    p.poll()
    agent.publish_condition("Fault", iso(5), message="boom")
    p.poll()
    assert len(store.active_conditions("X")) == 1

    agent.down = True
    p.poll()                                                     # outage: the logger can't see the clear
    agent.publish_condition("Normal", iso(9))
    agent.down = False
    p.poll()                                                     # resync via /current: fault is gone
    assert store.active_conditions("X") == {}
    assert [r[0] for r in condition_rows(store)] == ["FAULT", "NORMAL"]


def test_active_fault_survives_a_logger_restart_without_duplicating(agent, store):
    agent.publish("ACTIVE", iso(0))
    agent.publish_condition("Fault", iso(5), message="boom")
    poller(store, agent, CONDITION_TRACKING).poll()
    poller(store, agent, CONDITION_TRACKING).poll()              # a fresh poller, as after a restart
    assert [r[0] for r in condition_rows(store)] == ["FAULT"]


def test_label_keeps_two_agents_with_the_same_device_name_apart(agent, store):
    agent.publish("ACTIVE", iso(0))
    DevicePoller(store, requests.Session(), Source(agent.url, "X", "X-line2"), ("Execution",)).poll()
    assert "X-line2" in store.devices() and "X" not in store.devices()


# ---------- backfill ----------

def test_backfill_replays_history_in_order_and_normal_polling_continues(agent, store):
    agent.publish("ACTIVE", iso(0))
    agent.publish("READY", iso(10))
    agent.publish("ACTIVE", iso(35))
    p = DevicePoller(store, requests.Session(), Source(agent.url, "X"), ("Execution",), backfill=True)
    p.poll()
    assert states(store) == ["ACTIVE", "READY", "ACTIVE"]     # the whole buffer, not just the latest value
    p.poll()
    assert states(store) == ["ACTIVE", "READY", "ACTIVE"]     # nothing duplicated
    agent.publish("READY", iso(50))
    p.poll()
    assert states(store) == ["ACTIVE", "READY", "ACTIVE", "READY"]


def test_without_backfill_only_the_current_value_is_stored(agent, store):
    agent.publish("ACTIVE", iso(0))
    agent.publish("READY", iso(10))
    poller(store, agent).poll()
    assert states(store) == ["READY"]


def test_backfill_replays_condition_history(agent, store):
    agent.publish("ACTIVE", iso(0))
    agent.publish_condition("Normal", iso(0), code="")
    agent.publish_condition("Fault", iso(5), message="boom")
    agent.publish_condition("Normal", iso(7))
    p = DevicePoller(store, requests.Session(), Source(agent.url, "X"), ("Execution", "Condition"), backfill=True)
    p.poll()
    assert [r[0] for r in condition_rows(store)] == ["FAULT", "NORMAL"]
    assert store.active_conditions("X") == {}

