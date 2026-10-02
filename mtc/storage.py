"""Database access: Postgres (preferred) or SQLite (fallback) behind one small interface.

The schema lives in sql/postgres.sql and sql/sqlite.sql; this module only runs it and reads/writes rows.
All values are passed as query parameters, never formatted into SQL strings.
"""
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from mtc.models import ConditionEvent, Observation
from mtc.timeutil import format_ts, parse_ts

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"
DEFAULT_SQLITE_PATH = "mtc.db"


class Store:
    """Shared queries. Subclasses supply the connection, placeholder style and timestamp encoding."""

    placeholder = "?"
    label = ""  # shown in console output

    def __init__(self) -> None:
        self.connect()

    # --- subclass hooks -------------------------------------------------
    def connect(self) -> None:
        raise NotImplementedError

    def _encode_ts(self, moment: datetime):
        raise NotImplementedError

    def _decode_ts(self, raw) -> datetime:
        raise NotImplementedError

    def _commit(self) -> None:
        pass

    # --- connection lifecycle ------------------------------------------
    def reconnect(self) -> None:
        """Drop the connection and open a fresh one (used after a storage error)."""
        try:
            self.db.close()
        except Exception:
            pass
        self.connect()

    def close(self) -> None:
        self.db.close()

    # --- writes ---------------------------------------------------------
    def add_many(self, observations: Iterable[Observation]) -> None:
        p = self.placeholder
        sql = (f"INSERT INTO observations (ts, device, data_item_id, type, value, instance_id, sub_type) "
               f"VALUES ({p},{p},{p},{p},{p},{p},{p})")
        for o in observations:
            self.db.execute(sql, (self._encode_ts(o.ts), o.device, o.data_item_id,
                                  o.type, o.value, o.instance_id, o.sub_type))
        self._commit()

    def add_conditions(self, events: Iterable[ConditionEvent]) -> None:
        p = self.placeholder
        sql = (f"INSERT INTO conditions (ts, device, data_item_id, type, level, native_code, severity, "
               f"qualifier, message, instance_id) VALUES ({','.join([p] * 10)})")
        for e in events:
            self.db.execute(sql, (self._encode_ts(e.ts), e.device, e.data_item_id, e.type, e.level,
                                  e.native_code, e.severity, e.qualifier, e.message, e.instance_id))
        self._commit()

    # --- reads ----------------------------------------------------------
    def last_values(self, device: str) -> dict[str, tuple[str, str]]:
        """Latest stored (type, value) for each of the device's data items, keyed by data_item_id."""
        p = self.placeholder
        rows = self.db.execute(
            f"SELECT o.data_item_id, o.type, o.value FROM observations o "
            f"JOIN (SELECT data_item_id, MAX(id) AS last_id FROM observations "
            f"      WHERE device = {p} GROUP BY data_item_id) latest ON o.id = latest.last_id",
            (device,)).fetchall()
        return {item: (type_, value) for item, type_, value in rows}

    def last_instance_id(self, device: str) -> Optional[str]:
        p = self.placeholder
        row = self.db.execute(
            f"SELECT instance_id FROM observations WHERE device = {p} AND instance_id IS NOT NULL "
            f"ORDER BY id DESC LIMIT 1", (device,)).fetchone()
        return row[0] if row else None

    def active_conditions(self, device: str) -> dict[tuple[str, str], tuple[str, str, str]]:
        """Currently active conditions: (data_item_id, native_code) -> (level, message, type)."""
        p = self.placeholder
        rows = self.db.execute(
            f"SELECT data_item_id, native_code, level, message, type FROM active_conditions "
            f"WHERE device = {p}", (device,)).fetchall()
        return {(item, code): (level, message, type_) for item, code, level, message, type_ in rows}

    def devices(self) -> list[str]:
        return [row[0] for row in self.db.execute("SELECT DISTINCT device FROM observations ORDER BY 1")]

    def execution_events(self, device: str) -> list[tuple[datetime, str]]:
        """The device's Execution history as (time, state), oldest first."""
        p = self.placeholder
        rows = self.db.execute(
            f"SELECT ts, state FROM events WHERE device = {p} ORDER BY ts, id", (device,)).fetchall()
        return [(self._decode_ts(ts), state) for ts, state in rows]


class SqliteStore(Store):
    def __init__(self, path: str = DEFAULT_SQLITE_PATH):
        self.path = path
        self.label = path
        super().__init__()

    def connect(self) -> None:
        self.db = sqlite3.connect(self.path)
        schema = (SQL_DIR / "sqlite.sql").read_text()
        self.db.executescript(schema)
        self._add_sub_type_column_if_missing()
        if self._events_is_legacy_table():
            self._migrate_legacy_events(schema)

    def _add_sub_type_column_if_missing(self) -> None:
        """Databases created before sub_type existed get the column added (SQLite has no IF NOT EXISTS for this)."""
        columns = [row[1] for row in self.db.execute("PRAGMA table_info(observations)")]
        if "sub_type" not in columns:
            self.db.execute("ALTER TABLE observations ADD COLUMN sub_type TEXT")
            self.db.commit()

    def _events_is_legacy_table(self) -> bool:
        row = self.db.execute("SELECT type FROM sqlite_master WHERE name = 'events'").fetchone()
        return bool(row) and row[0] == "table"

    def _migrate_legacy_events(self, schema: str) -> None:
        """Copy rows of the old `events` table into observations, keep it as events_legacy, add the view."""
        self.db.execute(
            "INSERT INTO observations (ts, device, data_item_id, type, value, instance_id) "
            "SELECT ts, device, 'execution', 'Execution', state, instance_id FROM events ORDER BY rowid")
        # SQLite re-validates views on rename; drop the one that still points at the old table layout.
        self.db.execute("DROP VIEW IF EXISTS state_intervals")
        self.db.execute("ALTER TABLE events RENAME TO events_legacy")
        self.db.commit()
        self.db.executescript(schema)  # now the compatibility `events` view can be created

    def _encode_ts(self, moment: datetime) -> str:
        return format_ts(moment)

    def _decode_ts(self, raw: str) -> datetime:
        return parse_ts(raw)

    def _commit(self) -> None:
        self.db.commit()


class PostgresStore(Store):
    placeholder = "%s"
    label = "postgres"

    def __init__(self, url: str):
        self.url = url
        super().__init__()

    def connect(self) -> None:
        import psycopg  # imported here so SQLite-only users don't need it installed

        self.db = psycopg.connect(self.url, autocommit=True)
        self.db.execute((SQL_DIR / "postgres.sql").read_text())

    def _encode_ts(self, moment: datetime) -> datetime:
        return moment  # psycopg stores aware datetimes as timestamptz

    def _decode_ts(self, raw: datetime) -> datetime:
        return raw


def open_store(db_url: Optional[str] = None, sqlite_path: str = DEFAULT_SQLITE_PATH) -> Store:
    """Postgres when db_url is given, otherwise a SQLite file."""
    return PostgresStore(db_url) if db_url else SqliteStore(sqlite_path)
