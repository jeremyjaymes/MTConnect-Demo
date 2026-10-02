"""Turn Condition readings into the rows worth storing. Pure logic: no network, no database.

How MTConnect conditions work (confirmed on the Mazak agents): a condition is a Warning or Fault with a
native code and message; later a Normal carrying the same native code clears it. A Normal with no code
clears every active condition on that data item. Several conditions (different codes) can be active on
one data item at once.

We store only activations and the events that clear them. The Normal every item reports at startup is
noise, and an agent repeating the same Fault would otherwise be stored again.
"""
from datetime import datetime
from typing import Mapping, Optional, Sequence

from mtc.models import ConditionEvent, ConditionReading
from mtc.states import ACTIVE_LEVELS, NORMAL

# (data_item_id, native_code) identifies one condition.
Key = tuple[str, str]
# What we remember about an active condition: (level, message, type).
ActiveInfo = tuple[str, str, str]


class ConditionTracker:
    def __init__(self, device: str, active: Optional[Mapping[Key, ActiveInfo]] = None):
        """`active` is the set of currently active conditions already stored in the database."""
        self.device = device
        self._active: dict[Key, ActiveInfo] = dict(active or {})

    def apply(self, readings: Sequence[ConditionReading], instance_id: Optional[str], polled_at: datetime,
              full_state: bool = False) -> list[ConditionEvent]:
        """Return the events to store.

        `full_state` is True for a /current response, which lists every active condition. Anything we
        think is active but the agent no longer lists was cleared while we weren't watching (a restart,
        an outage, a buffer wrap), so we record it as cleared at the poll time.
        """
        events: list[ConditionEvent] = []
        for reading in readings:
            key = (reading.data_item_id, reading.native_code)
            if reading.level in ACTIVE_LEVELS:
                info = (reading.level, reading.message, reading.type)
                if self._active.get(key) != info:
                    self._active[key] = info
                    events.append(self._event(reading, instance_id))
            else:
                cleared = [k for k in self._active
                           if k[0] == reading.data_item_id and (not reading.native_code or k[1] == reading.native_code)]
                for k in cleared:
                    del self._active[k]
                if cleared:
                    events.append(self._event(reading, instance_id))

        if full_state:
            listed_items = {reading.data_item_id for reading in readings}
            still_active = {(r.data_item_id, r.native_code) for r in readings if r.level in ACTIVE_LEVELS}
            for key in [k for k in self._active if k[0] in listed_items and k not in still_active]:
                _, _, type_ = self._active.pop(key)
                events.append(ConditionEvent(polled_at, self.device, key[0], type_, NORMAL, key[1],
                                             None, None, "", instance_id))
        return events

    def _event(self, reading: ConditionReading, instance_id: Optional[str]) -> ConditionEvent:
        return ConditionEvent(reading.ts, self.device, reading.data_item_id, reading.type, reading.level,
                              reading.native_code, reading.severity, reading.qualifier, reading.message,
                              instance_id)
