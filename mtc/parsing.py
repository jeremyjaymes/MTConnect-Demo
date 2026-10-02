"""Turn MTConnect XML (/current or /sample responses, or an error document) into plain objects.

Pure: no network, no database.
"""
import xml.etree.ElementTree as ET
from typing import Iterable, Optional

from mtc.models import ConditionReading, Reading, Snapshot
from mtc.states import CONDITION, CONDITION_LEVELS, UNAVAILABLE
from mtc.timeutil import parse_ts


class AgentError(Exception):
    """The agent answered with an MTConnectError document (e.g. OUT_OF_RANGE, INVALID_XPATH)."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def _local_name(tag: str) -> str:
    """Strip the XML namespace. Namespace URIs differ between agent versions, so never match on them."""
    return tag.rsplit("}", 1)[-1]


def _int_or_none(text: Optional[str]) -> Optional[int]:
    return int(text) if text and text.isdigit() else None


def parse_streams(xml_bytes: bytes, tracked_types: Iterable[str]) -> Snapshot:
    """Extract the Header and every element whose name is in tracked_types, ordered by sequence.

    Raises AgentError for an MTConnectError document, ET.ParseError for invalid XML.
    """
    tracked = set(tracked_types)
    root = ET.fromstring(xml_bytes)
    if _local_name(root.tag) == "MTConnectError":
        error = next((e for e in root.iter() if _local_name(e.tag) == "Error"), None)
        raise AgentError(error.get("errorCode", "UNKNOWN") if error is not None else "UNKNOWN",
                         (error.text or "").strip() if error is not None else "")

    instance_id = creation_time = next_sequence = last_sequence = first_sequence = None
    readings, conditions = [], []
    for element in root.iter():
        name = _local_name(element.tag)
        if name == "Header" and instance_id is None:
            instance_id = element.get("instanceId")
            if element.get("creationTime"):
                creation_time = parse_ts(element.get("creationTime"))
            next_sequence = _int_or_none(element.get("nextSequence"))
            last_sequence = _int_or_none(element.get("lastSequence"))
            first_sequence = _int_or_none(element.get("firstSequence"))
        elif CONDITION in tracked and name in CONDITION_LEVELS and element.get("dataItemId") \
                and element.get("timestamp"):
            conditions.append(ConditionReading(
                data_item_id=element.get("dataItemId"),
                type=element.get("type", ""),
                level=CONDITION_LEVELS[name],
                native_code=element.get("nativeCode", ""),
                severity=element.get("nativeSeverity"),
                qualifier=element.get("qualifier"),
                message=(element.text or "").strip(),
                ts=parse_ts(element.get("timestamp")),
                sequence=_int_or_none(element.get("sequence")) or 0,
            ))
        elif name in tracked and element.get("dataItemId") and element.get("timestamp"):
            readings.append(Reading(
                data_item_id=element.get("dataItemId"),
                type=name,
                value=(element.text or "").strip() or UNAVAILABLE,
                ts=parse_ts(element.get("timestamp")),
                sequence=_int_or_none(element.get("sequence")) or 0,
                sub_type=element.get("subType"),
            ))
    readings.sort(key=lambda reading: reading.sequence)
    conditions.sort(key=lambda condition: condition.sequence)
    return Snapshot(instance_id, creation_time, tuple(readings), next_sequence, last_sequence,
                    tuple(conditions), first_sequence)
