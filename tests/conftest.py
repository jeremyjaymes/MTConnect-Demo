"""Shared fixtures: a stateful fake MTConnect agent and stores to test against."""
import http.server
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from mtc.storage import PostgresStore, SqliteStore
from mtc.timeutil import format_ts, utc_now

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def at(seconds: float) -> datetime:
    """A time `seconds` after a fixed origin, so expected durations are exact."""
    return T0 + timedelta(seconds=seconds)


def iso(seconds: float) -> str:
    return format_ts(at(seconds))


class FakeAgent:
    """Behaves like an MTConnect agent: a circular buffer of numbered observations.

    Tests call publish()/restart()/trim() to change what the "machine" did, and set `down` to simulate
    an outage. `requests_seen` records every request path+query so tests can check how we talk to it.
    """

    def __init__(self):
        self.instance = 1
        self.next_seq = 1
        self.first_seq = 1
        self.log: list[tuple] = []  # (sequence, data_item_id, element, text, ts, attributes)
        self.down = False
        self.requests_seen: list[str] = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                outer.requests_seen.append(self.path)
                status, body = outer.respond(self.path)
                self.send_response(status)
                self.end_headers()
                self.wfile.write(body)

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    # --- what the "machine" does ---------------------------------------
    def publish(self, state: str, ts: str, program: str = None) -> None:
        self._add("execution", "Execution", state, ts)
        if program:
            self._add("program", "Program", program, ts)

    def publish_condition(self, level: str, ts: str, item: str = "system_cond", type_: str = "SYSTEM",
                          code: str = "128", message: str = "") -> None:
        """level is Normal/Warning/Fault/Unavailable. Attributes mirror what the Mazak agents send."""
        attributes = {"type": type_}
        if code:
            attributes["nativeCode"] = code
        self._add(item, level, message, ts, attributes)

    def restart(self) -> None:
        """New instanceId and an empty buffer, as after an agent reboot."""
        self.instance += 1
        self.log, self.next_seq, self.first_seq = [], 1, 1

    def trim(self) -> None:
        """Drop everything buffered, as if the circular buffer wrapped past all of it."""
        self.first_seq = self.next_seq
        self.log = []

    def _add(self, item, type_, value, ts, attributes=None):
        self.log.append((self.next_seq, item, type_, value, ts, attributes or {}))
        self.next_seq += 1

    # --- HTTP side ------------------------------------------------------
    def header(self, next_seq: int = None) -> str:
        next_seq = next_seq or self.next_seq
        return (f'<Header instanceId="{self.instance}" creationTime="{format_ts(utc_now())}" '
                f'nextSequence="{next_seq}" firstSequence="{self.first_seq}" '
                f'lastSequence="{self.next_seq - 1}"/>')

    def error(self, code: str, text: str):
        return 400, (f'<MTConnectError xmlns="urn:fake:error"><Header instanceId="{self.instance}"/>'
                     f'<Errors><Error errorCode="{code}">{text}</Error></Errors></MTConnectError>').encode()

    def respond(self, raw_path: str):
        if self.down:
            return 500, b"down"
        url = urlparse(raw_path)
        query = parse_qs(url.query)
        wanted = set(re.findall(r'@id="([^"]+)"', query.get("path", [""])[0]))
        rows = [r for r in self.log if not wanted or r[1] in wanted]

        if url.path.endswith("/current"):
            rows = self.current_rows(rows)
        elif url.path.endswith("/sample"):
            start = int(query.get("from", [self.first_seq])[0])
            count = int(query.get("count", [100])[0])
            if start < self.first_seq:
                return self.error("OUT_OF_RANGE", f"'from' must be greater than {self.first_seq - 1}")
            matching = [r for r in rows if r[0] >= start]
            batch = matching[:count]
            # A truncated batch continues after its last row; otherwise we have reached the end of the buffer.
            next_seq = batch[-1][0] + 1 if len(matching) > count else self.next_seq
            return 200, self.render(batch, self.header(next_seq))
        else:
            return 404, b"not found"
        return 200, self.render(rows, self.header())

    CONDITION_ELEMENTS = {"Normal", "Warning", "Fault", "Unavailable"}

    def current_rows(self, rows):
        """Newest value per data item; for conditions, every active one (or a Normal if none is)."""
        latest, active = {}, {}
        for row in rows:
            sequence, item, element, _, _, attributes = row
            if element not in self.CONDITION_ELEMENTS:
                latest[item] = row
            elif element in ("Warning", "Fault"):
                active.setdefault(item, {})[attributes.get("nativeCode", "")] = row
                latest.pop(item, None)
            else:
                code = attributes.get("nativeCode", "")
                if code:
                    active.get(item, {}).pop(code, None)
                else:
                    active.pop(item, None)
                if not active.get(item):
                    latest[item] = row
        return list(latest.values()) + [row for per_item in active.values() for row in per_item.values()]

    def render(self, rows, header) -> bytes:
        items = "".join(
            f'<{t} dataItemId="{i}" sequence="{n}" timestamp="{ts}"'
            + "".join(f' {k}="{v}"' for k, v in attributes.items()) + f'>{v}</{t}>'
            for n, i, t, v, ts, attributes in rows)
        return (f'<MTConnectStreams xmlns="urn:fake:1">{header}<Streams><DeviceStream><ComponentStream>'
                f'<Events>{items}</Events></ComponentStream></DeviceStream></Streams>'
                f'</MTConnectStreams>').encode()

    def stop(self):
        self.server.shutdown()


@pytest.fixture
def agent():
    fake = FakeAgent()
    yield fake
    fake.stop()


@pytest.fixture(params=["sqlite", "postgres"])
def store(request, tmp_path):
    """A store on SQLite, and on Postgres when MTC_TEST_PG_URL is set (only test devices 'X' / 'X-line2' are touched)."""
    if request.param == "postgres":
        url = os.environ.get("MTC_TEST_PG_URL")
        if not url:
            pytest.skip("set MTC_TEST_PG_URL to run against Postgres")
        s = PostgresStore(url)
        clean = lambda: [s.db.execute(f"DELETE FROM {table} WHERE device IN ('X', 'X-line2')")
                         for table in ("observations", "conditions")]
        clean()
        yield s
        clean()
    else:
        s = SqliteStore(str(tmp_path / "t.db"))
        yield s
    s.close()
