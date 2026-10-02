"""Print the utilization report for devices in a store."""
from datetime import datetime
from typing import Optional

from mtc.intervals import compute_intervals, unmeasured_seconds, utilization
from mtc.storage import Store


def print_device_report(store: Store, device: str, start: Optional[datetime], end: Optional[datetime]) -> None:
    events = store.execution_events(device)
    if not events:
        print(f"no events for {device} in {store.label}")
        return

    seconds_by_state, window = compute_intervals(events, start, end)
    print(f"device {device}  window {window:.0f}s")
    for state, seconds in sorted(seconds_by_state.items(), key=lambda item: -item[1]):
        percent = 100 * seconds / window if window else 0
        print(f"  {state:<16}{seconds:>12.1f}s {percent:>7.2f}%")

    result = utilization(seconds_by_state, window)
    excluded = unmeasured_seconds(seconds_by_state)
    shown = f"{100 * result:.2f}%" if result is not None else "n/a"
    print(f"\nUTILIZATION = ACTIVE / (window - UNAVAILABLE/AGENT_RESTART {excluded:.0f}s) = {shown}\n")


def print_report(store: Store, devices: list[str], start: Optional[datetime], end: Optional[datetime]) -> None:
    for device in devices or store.devices():  # default: every device in the database
        print_device_report(store, device, start, end)
