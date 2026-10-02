"""Decide which readings are worth storing. Pure logic: it never touches the network or the database.

A DeviceTracker remembers the last value of every data item it has seen for one device and turns each
new Snapshot into the list of Changes to store:
  * a value is stored only when it differs from the last stored value;
  * if the agent's instanceId changed, every known item first gets an AGENT_RESTART marker;
  * if a poll fails, every known item gets an UNAVAILABLE marker (once, not on every failed poll);
  * if observations were lost (agent buffer wrapped), every known item gets a DATA_GAP marker.

Why markers get special timestamps: an agent stamps an unchanged state with when it BEGAN, which can be
hours old. A marker is stamped with the poll time, so the first real value after a marker must be stamped
no earlier than that poll time too; otherwise sorting by time would put the marker last and a recovered
machine would look unavailable until its next change.
"""
from datetime import datetime
from typing import Mapping, Optional

from mtc.models import Change, Observation, Snapshot
from mtc.states import AGENT_RESTART, DATA_GAP, UNAVAILABLE, UNMEASURED


class DeviceTracker:
    def __init__(self, device: str,
                 known: Optional[Mapping[str, tuple[str, str]]] = None,
                 instance_id: Optional[str] = None):
        """known maps data_item_id -> (type, last stored value); both come from the database on startup."""
        self.device = device
        self.instance_id = instance_id
        self._types: dict[str, str] = {item: type_ for item, (type_, _) in (known or {}).items()}
        self._last: dict[str, str] = {item: value for item, (_, value) in (known or {}).items()}
        self._in_gap: set[str] = {item for item, value in self._last.items() if value in UNMEASURED}

    def apply(self, snapshot: Snapshot, polled_at: datetime) -> list[Change]:
        """Return the changes to store for a successful poll."""
        changes: list[Change] = []

        if self.instance_id is not None and snapshot.instance_id != self.instance_id:
            changes += self._mark_all(AGENT_RESTART, polled_at, snapshot.instance_id)
        self.instance_id = snapshot.instance_id

        for reading in snapshot.readings:
            if self._last.get(reading.data_item_id) == reading.value:
                self._in_gap.discard(reading.data_item_id)  # e.g. the agent itself still reports UNAVAILABLE
                continue
            ts = reading.ts
            if reading.data_item_id in self._in_gap:
                ts = max(reading.ts, polled_at)  # never earlier than the marker that precedes it
                self._in_gap.discard(reading.data_item_id)
            observation = Observation(ts, self.device, reading.data_item_id, reading.type,
                                      reading.value, snapshot.instance_id, reading.sub_type)
            changes.append(Change(observation, self._last.get(reading.data_item_id)))
            self._types[reading.data_item_id] = reading.type
            self._last[reading.data_item_id] = reading.value
        return changes

    def mark_unavailable(self, ts: datetime) -> list[Change]:
        """Markers for a failed poll. Items already in a gap are skipped, so repeated failures add nothing."""
        return self._mark_all(UNAVAILABLE, ts, instance_id=None)

    def mark_data_gap(self, ts: datetime) -> list[Change]:
        """Markers for lost observations (the agent's buffer wrapped past our position)."""
        return self._mark_all(DATA_GAP, ts, instance_id=None)

    def _mark_all(self, value: str, ts: datetime, instance_id: Optional[str]) -> list[Change]:
        changes = []
        for item in list(self._last):
            if item in self._in_gap or self._last[item] == value:
                continue
            changes.append(Change(Observation(ts, self.device, item, self._types[item], value, instance_id),
                                  self._last[item]))
            self._last[item] = value
            self._in_gap.add(item)
        return changes
