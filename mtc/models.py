"""Plain data records passed between modules (no behavior, no I/O)."""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class Reading:
    """One value read from an agent response."""
    data_item_id: str
    type: str  # MTConnect element name, e.g. "Execution" or "Program"
    value: str
    ts: datetime  # the observation's own timestamp, not the poll time
    sequence: int = 0  # agent-assigned order; the agent groups XML by component, not by sequence
    sub_type: Optional[str] = None  # e.g. "x:CUT"; stays the same across agents even when item ids differ


@dataclass(frozen=True)
class ConditionReading:
    """One Condition element (Normal / Warning / Fault / Unavailable) read from an agent response."""
    data_item_id: str
    type: str          # what the condition is about, e.g. "SYSTEM", "LOAD"
    level: str         # NORMAL, WARNING, FAULT or UNAVAILABLE
    native_code: str   # the machine's own code; "" when it has none
    severity: Optional[str]
    qualifier: Optional[str]
    message: str
    ts: datetime
    sequence: int = 0


@dataclass(frozen=True)
class Snapshot:
    """The parsed result of one /current request."""
    instance_id: Optional[str]
    creation_time: Optional[datetime]
    readings: tuple[Reading, ...]
    next_sequence: Optional[int] = None  # where to continue with /sample?from=
    last_sequence: Optional[int] = None  # newest sequence in the agent's buffer
    conditions: tuple[ConditionReading, ...] = ()
    first_sequence: Optional[int] = None  # oldest sequence still in the agent's buffer


@dataclass(frozen=True)
class Observation:
    """One stored row: a value a device's data item took at a point in time."""
    ts: datetime
    device: str
    data_item_id: str
    type: str
    value: str
    instance_id: Optional[str]
    sub_type: Optional[str] = None


@dataclass(frozen=True)
class Change:
    """An observation to store, plus the value it replaced (None the first time)."""
    observation: Observation
    old_value: Optional[str]


@dataclass(frozen=True)
class ConditionEvent:
    """One stored condition row: a warning/fault starting, or what cleared it."""
    ts: datetime
    device: str
    data_item_id: str
    type: str
    level: str
    native_code: str
    severity: Optional[str]
    qualifier: Optional[str]
    message: str
    instance_id: Optional[str]


@dataclass(frozen=True)
class Source:
    """Where to read a device from. `label` is the name stored in the database (defaults to the device)."""
    agent_url: str
    device: str
    label: Optional[str] = None

    @property
    def name(self) -> str:
        return self.label or self.device
