"""The polling loop. Network and database calls live here; the decisions are in tracker.py and parsing.py.

Following the MTConnect client pattern:
  1. GET /current            -> current values, plus the header's nextSequence
  2. GET /sample?from=<next> -> only observations since the last poll, in order, none missed or repeated
  3. If the agent's instanceId changed, sequence numbers are no longer valid: go back to step 1.
  4. If our position fell out of the agent's buffer (OUT_OF_RANGE), observations were lost: record a
     DATA_GAP marker and go back to step 1.
The `path` parameter limits /sample to the data items we log, which keeps responses small.
"""
import sys
import time
import xml.etree.ElementTree as ET
from typing import NamedTuple, Optional, Sequence

import requests

from mtc.conditions import ConditionTracker
from mtc.models import Change, ConditionEvent, Snapshot, Source
from mtc.parsing import AgentError, parse_streams
from mtc.storage import Store
from mtc.timeutil import format_ts, utc_now
from mtc.tracker import DeviceTracker

REQUEST_TIMEOUT_SECONDS = 10
SAMPLE_COUNT = 1000   # observations per /sample request (agents cap this; Mazak's cap is 4096)
MAX_BATCHES = 20      # catch-up requests per poll, so one poll can never loop forever


class ReadResult(NamedTuple):
    snapshot: Snapshot
    lost_data: bool   # the agent's buffer wrapped past us, so observations were lost
    full_state: bool  # from /current: lists everything that is active right now, not just changes


class DevicePoller:
    """Polls one device on one agent and writes what changed."""

    def __init__(self, store: Store, session: requests.Session, source: Source, tracked_types: Sequence[str],
                 backfill: bool = False):
        self.store, self.session, self.source = store, session, source
        self.backfill_pending = backfill  # on the first poll, replay what the agent still buffers
        name = source.name  # the name stored in the database
        self.base_url = f"{source.agent_url.rstrip('/')}/{source.device}"
        self.tracked_types = list(tracked_types)
        self.tracker = DeviceTracker(name, store.last_values(name), store.last_instance_id(name))
        self.condition_tracker = ConditionTracker(name, store.active_conditions(name))
        self.next_sequence: Optional[int] = None  # None means "resync with /current before sampling"
        self.item_ids: set[str] = set()           # data items to request; learned from /current

    def poll(self) -> None:
        """Poll once. A failed request is recorded as UNAVAILABLE; it never raises (storage errors do)."""
        polled_at = utc_now()
        changes: list[Change] = []
        condition_events: list[ConditionEvent] = []
        try:
            for result in self._read():
                snapshot = result.snapshot
                if result.lost_data:
                    changes += self.tracker.mark_data_gap(polled_at)
                changes += self.tracker.apply(snapshot, polled_at)
                condition_events += self.condition_tracker.apply(snapshot.conditions, snapshot.instance_id,
                                                                 polled_at, result.full_state)
        except (requests.RequestException, ET.ParseError, AgentError, ValueError) as error:
            print(f"{format_ts(polled_at)} {self.source.name} error: {error}", file=sys.stderr, flush=True)
            self.next_sequence = None  # observations may have been missed: resync once the agent answers
            if isinstance(error, AgentError) and error.code == "INVALID_XPATH":
                self.item_ids = set()  # our item filter was rejected: ask for everything next time
            changes = self.tracker.mark_unavailable(polled_at)  # no observation exists, so use local time

        self.store.add_many(change.observation for change in changes)
        self.store.add_conditions(condition_events)
        for change in changes:
            print_change(change)
        for event in condition_events:
            print_condition(event)

    # --- reading from the agent ----------------------------------------
    def _read(self) -> list[ReadResult]:
        """What to apply this poll, oldest first. Usually one result; the first poll of a backfill has two."""
        if self.next_sequence is None:
            current = self._resync()
            results = []
            if self.backfill_pending:
                self.backfill_pending = False
                history = self._backfill(current)
                if history is not None:
                    results.append(ReadResult(history, lost_data=False, full_state=False))
            # Applied after the history so items that never changed in the buffer (e.g. the program name) are
            # still recorded, and nothing already replayed is stored twice.
            results.append(ReadResult(current, lost_data=False, full_state=True))
            return results
        try:
            snapshot = self._sample()
        except AgentError as error:
            if error.code == "OUT_OF_RANGE":  # our position left the agent's circular buffer
                return [ReadResult(self._resync(), lost_data=True, full_state=True)]
            raise
        if snapshot is None:  # instanceId changed: the agent restarted
            return [ReadResult(self._resync(), lost_data=False, full_state=True)]
        return [ReadResult(snapshot, lost_data=False, full_state=False)]

    def _backfill(self, current: Snapshot) -> Optional[Snapshot]:
        """Read everything the agent still buffers, from its oldest sequence forward."""
        if current.first_sequence is None:
            return None
        self.next_sequence = current.first_sequence
        return self._sample()  # None if the agent restarted since we last stored anything

    def _resync(self) -> Snapshot:
        """GET /current, remember which items to track and where to continue sampling."""
        snapshot = self._fetch("current")
        if not snapshot.readings and not snapshot.conditions:
            raise ValueError(f"response has none of the tracked items: {', '.join(self.tracked_types)}")
        if snapshot.next_sequence is None:
            raise ValueError("response header has no nextSequence")
        # Every condition item is requested even while Normal, so a later Fault on it is not filtered out.
        self.item_ids |= {reading.data_item_id for reading in snapshot.readings}
        self.item_ids |= {condition.data_item_id for condition in snapshot.conditions}
        self.next_sequence = snapshot.next_sequence
        return snapshot

    def _sample(self) -> Optional[Snapshot]:
        """GET /sample from our position, catching up in batches. None if the agent restarted."""
        readings, conditions, position, last = [], [], self.next_sequence, None
        for _ in range(MAX_BATCHES):
            batch = self._fetch("sample", **{"from": position, "count": SAMPLE_COUNT})
            if self.tracker.instance_id is not None and batch.instance_id != self.tracker.instance_id:
                return None
            if batch.next_sequence is None:
                raise ValueError("response header has no nextSequence")
            readings += batch.readings
            conditions += batch.conditions
            position, last = batch.next_sequence, batch
            if batch.last_sequence is None or batch.next_sequence > batch.last_sequence:
                break  # caught up
        self.next_sequence = position
        return Snapshot(last.instance_id, last.creation_time, tuple(readings), position, last.last_sequence,
                        tuple(conditions))

    def _fetch(self, endpoint: str, **params) -> Snapshot:
        if self.item_ids:
            params["path"] = "|".join(f'//DataItem[@id="{item}"]' for item in sorted(self.item_ids))
        response = self.session.get(f"{self.base_url}/{endpoint}", params=params,
                                    timeout=REQUEST_TIMEOUT_SECONDS)
        try:
            return parse_streams(response.content, self.tracked_types)  # raises AgentError on error documents
        except ET.ParseError:
            response.raise_for_status()  # a non-XML error page: report the HTTP status instead
            raise


def print_change(change: Change) -> None:
    o = change.observation
    print(f"{format_ts(o.ts)} {o.device} {o.type} {change.old_value or '(none)'} -> {o.value}", flush=True)


def print_condition(event: ConditionEvent) -> None:
    detail = f" {event.native_code}" if event.native_code else ""
    message = f' "{event.message}"' if event.message else ""
    print(f"{format_ts(event.ts)} {event.device} Condition {event.level} {event.type}{detail}{message}", flush=True)


def run(store: Store, sources: Sequence[Source], tracked_types: Sequence[str], poll_seconds: float,
        backfill: bool = False) -> None:
    """Poll every source forever, until Ctrl+C."""
    session = requests.Session()
    pollers = {source.name: DevicePoller(store, session, source, tracked_types, backfill) for source in sources}
    print(f"logging {', '.join(pollers)} ({', '.join(tracked_types)}) every {poll_seconds}s "
          f"into {store.label}; Ctrl+C to stop", flush=True)
    try:
        while True:
            for source in sources:
                try:
                    pollers[source.name].poll()
                except Exception as error:  # storage failure: reconnect and rebuild state from the DB
                    print(f"{format_ts(utc_now())} {source.name} storage error: {error}", file=sys.stderr, flush=True)
                    try:
                        store.reconnect()
                        pollers[source.name] = DevicePoller(store, session, source, tracked_types)  # no backfill on reconnect
                    except Exception as reconnect_error:
                        print(f"reconnect failed: {reconnect_error}", file=sys.stderr, flush=True)
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        pass
    finally:
        store.close()
        print("\nstopped")
