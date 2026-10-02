"""Migration of the old `events` table into `observations`."""
import sqlite3

from mtc.storage import SqliteStore


def test_legacy_events_table_is_migrated_and_kept_as_backup(tmp_path):
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE events (ts TEXT, device TEXT, state TEXT, instance_id TEXT);
        CREATE VIEW state_intervals AS SELECT device, state FROM events;
        INSERT INTO events VALUES ('2026-01-01T00:00:00Z', 'X', 'ACTIVE', '1');
        INSERT INTO events VALUES ('2026-01-01T00:00:10Z', 'X', 'READY', '1');
    """)
    old.commit()
    old.close()

    store = SqliteStore(path)
    assert [s for _, s in store.execution_events("X")] == ["ACTIVE", "READY"]
    assert store.last_values("X") == {"execution": ("Execution", "READY")}
    assert store.db.execute("SELECT COUNT(*) FROM events_legacy").fetchone()[0] == 2
    store.close()
    SqliteStore(path).close()  # reopening is a no-op (idempotent)


def test_database_created_before_sub_type_gets_the_column(tmp_path):
    path = str(tmp_path / "pre.db")
    old = sqlite3.connect(path)
    old.executescript("CREATE TABLE observations (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, "
                      "device TEXT NOT NULL, data_item_id TEXT NOT NULL, type TEXT NOT NULL, value TEXT NOT NULL, "
                      "instance_id TEXT);")
    old.commit()
    old.close()
    store = SqliteStore(path)
    assert "sub_type" in [r[1] for r in store.db.execute("PRAGMA table_info(observations)")]
    store.close()
