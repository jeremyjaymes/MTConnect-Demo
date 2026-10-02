"""Change detection, markers and resume behavior, with hand-built snapshots (no network, no DB)."""
from mtc.models import Reading, Snapshot
from mtc.tracker import DeviceTracker
from tests.conftest import at


def snap(*readings, instance="1"):
    return Snapshot(instance, at(0), tuple(Reading(i, t, v, at(s)) for i, t, v, s in readings))


def values(changes):
    return [(c.observation.data_item_id, c.observation.value) for c in changes]


def test_stores_only_changes_per_item():
    t = DeviceTracker("X")
    assert values(t.apply(snap(("e", "Execution", "ACTIVE", 0), ("p", "Program", "O1", 0)), at(1))) == \
        [("e", "ACTIVE"), ("p", "O1")]
    assert values(t.apply(snap(("e", "Execution", "ACTIVE", 0), ("p", "Program", "O2", 5)), at(6))) == [("p", "O2")]


def test_several_readings_of_one_item_in_a_single_snapshot_are_all_kept():
    t = DeviceTracker("X")
    changes = t.apply(snap(("e", "Execution", "ACTIVE", 0), ("e", "Execution", "READY", 5),
                           ("e", "Execution", "ACTIVE", 9)), at(10))
    assert [c.observation.value for c in changes] == ["ACTIVE", "READY", "ACTIVE"]


def test_resumes_from_stored_values_without_double_logging():
    t = DeviceTracker("X", known={"e": ("Execution", "ACTIVE")}, instance_id="1")
    assert t.apply(snap(("e", "Execution", "ACTIVE", 0)), at(1)) == []


def test_unavailable_marked_once_and_first_value_after_it_is_stamped_no_earlier_than_the_poll():
    t = DeviceTracker("X")
    t.apply(snap(("e", "Execution", "READY", 0)), at(1))
    assert values(t.mark_unavailable(at(100))) == [("e", "UNAVAILABLE")]
    assert t.mark_unavailable(at(102)) == []                                  # repeated failures add nothing
    change = t.apply(snap(("e", "Execution", "READY", 0)), at(200))[0]        # the state began at t=0 ...
    assert change.observation.ts == at(200)                                   # ... but is stamped at the poll
    # values after that keep their own timestamps
    assert t.apply(snap(("e", "Execution", "ACTIVE", 205)), at(210))[0].observation.ts == at(205)


def test_agent_reporting_unavailable_itself_is_not_shifted():
    t = DeviceTracker("X")
    t.apply(snap(("e", "Execution", "UNAVAILABLE", 0)), at(1))
    assert t.mark_unavailable(at(5)) == []                     # already UNAVAILABLE: no marker
    assert t.apply(snap(("e", "Execution", "READY", 50)), at(60))[0].observation.ts == at(50)


def test_nothing_to_mark_when_no_item_was_ever_seen():
    assert DeviceTracker("X").mark_unavailable(at(1)) == []


def test_instance_change_inserts_restart_marker_before_new_value():
    t = DeviceTracker("X")
    t.apply(snap(("e", "Execution", "ACTIVE", 0), instance="1"), at(1))
    changes = t.apply(snap(("e", "Execution", "ACTIVE", 0), instance="2"), at(60))
    assert values(changes) == [("e", "AGENT_RESTART"), ("e", "ACTIVE")]
    assert changes[0].observation.ts == at(60) == changes[1].observation.ts
    assert changes[1].old_value == "AGENT_RESTART"


def test_data_gap_marker():
    t = DeviceTracker("X")
    t.apply(snap(("e", "Execution", "ACTIVE", 0)), at(1))
    assert values(t.mark_data_gap(at(5))) == [("e", "DATA_GAP")]
