import pytest

from mtc.parsing import AgentError, parse_streams

XML = (b'<MTConnectStreams xmlns="urn:mtconnect.org:MTConnectStreams:9.9">'
       b'<Header instanceId="7" creationTime="2026-01-01T00:00:00Z"/>'
       b'<Execution dataItemId="e" timestamp="2026-01-01T00:00:00.5Z">FEED_HOLD</Execution>'
       b'<Program dataItemId="p" timestamp="2026-01-01T00:00:01Z">O1234</Program>'
       b'<Position dataItemId="x" timestamp="2026-01-01T00:00:01Z">1.5</Position>'
       b'<Execution dataItemId="blank" timestamp="2026-01-01T00:00:01Z"></Execution>'
       b'</MTConnectStreams>')


def test_reads_header_and_tracked_items_ignoring_namespace():
    snapshot = parse_streams(XML, ["Execution", "Program"])
    assert snapshot.instance_id == "7"
    by_id = {r.data_item_id: r for r in snapshot.readings}
    assert set(by_id) == {"e", "p", "blank"}  # Position is not tracked
    assert (by_id["e"].type, by_id["e"].value) == ("Execution", "FEED_HOLD")
    assert by_id["p"].value == "O1234"


def test_empty_value_is_unavailable():
    snapshot = parse_streams(XML, ["Execution"])
    assert {r.data_item_id: r.value for r in snapshot.readings}["blank"] == "UNAVAILABLE"


def test_uses_observation_timestamp():
    snapshot = parse_streams(XML, ["Execution"])
    first = [r for r in snapshot.readings if r.data_item_id == "e"][0]
    assert first.ts.microsecond == 500000


def test_readings_are_ordered_by_sequence_and_header_sequences_are_read():
    xml = (b'<MTConnectStreams xmlns="urn:x"><Header instanceId="1" nextSequence="12" lastSequence="11"/>'
           b'<Execution dataItemId="b" sequence="9" timestamp="2026-01-01T00:00:09Z">READY</Execution>'
           b'<Execution dataItemId="a" sequence="3" timestamp="2026-01-01T00:00:03Z">ACTIVE</Execution>'
           b'</MTConnectStreams>')
    snapshot = parse_streams(xml, ["Execution"])
    assert [r.data_item_id for r in snapshot.readings] == ["a", "b"]
    assert (snapshot.next_sequence, snapshot.last_sequence) == (12, 11)


def test_error_document_raises_agent_error_with_code():
    xml = (b'<MTConnectError xmlns="urn:x"><Errors><Error errorCode="OUT_OF_RANGE">'
           b"'from' must be greater than 5</Error></Errors></MTConnectError>")
    with pytest.raises(AgentError) as caught:
        parse_streams(xml, ["Execution"])
    assert caught.value.code == "OUT_OF_RANGE"


def test_condition_elements_keep_code_severity_qualifier_and_message():
    xml = (b'<MTConnectStreams xmlns="urn:x"><Header instanceId="1"/>'
           b'<Warning dataItemId="Sload_cond" sequence="5" timestamp="2026-10-01T19:21:11.468535Z" '
           b'type="LOAD" nativeCode="99999" nativeSeverity="25" qualifier="HIGH">Spindle1_load</Warning>'
           b'<Normal dataItemId="servo_cond" sequence="2" timestamp="2026-09-29T12:16:51Z" type="ACTUATOR"/>'
           b'</MTConnectStreams>')
    conditions = parse_streams(xml, ["Condition"]).conditions
    assert [c.level for c in conditions] == ["NORMAL", "WARNING"]           # ordered by sequence
    warning = conditions[1]
    assert (warning.type, warning.native_code, warning.severity, warning.qualifier, warning.message) == \
        ("LOAD", "99999", "25", "HIGH", "Spindle1_load")
    assert conditions[0].native_code == ""
    assert parse_streams(xml, ["Execution"]).conditions == ()               # only when Condition is tracked


def test_sub_type_is_read_from_observations():
    xml = (b'<MTConnectStreams xmlns="urn:x"><Header instanceId="1"/>'
           b'<AccumulatedTime dataItemId="ctime" name="cut_time" subType="x:CUT" sequence="1" '
           b'timestamp="2026-01-01T00:00:00Z">35570364</AccumulatedTime>'
           b'<Program dataItemId="p" sequence="2" timestamp="2026-01-01T00:00:00Z">O1</Program></MTConnectStreams>')
    by_id = {r.data_item_id: r for r in parse_streams(xml, ["AccumulatedTime", "Program"]).readings}
    assert by_id["ctime"].sub_type == "x:CUT" and by_id["p"].sub_type is None
