-- Idempotent SQLite twin of postgres.sql. Timestamps are ISO-8601 UTC text.
CREATE TABLE IF NOT EXISTS observations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    device       TEXT NOT NULL,
    data_item_id TEXT NOT NULL,
    type         TEXT NOT NULL,
    value        TEXT NOT NULL,
    instance_id  TEXT,
    sub_type     TEXT
);
CREATE INDEX IF NOT EXISTS observations_device_ts_idx ON observations (device, ts);
CREATE INDEX IF NOT EXISTS observations_device_item_ts_idx ON observations (device, data_item_id, ts);

-- No-op while the old `events` table still exists; the migration in storage.py renames it, then re-runs this file.
CREATE VIEW IF NOT EXISTS events AS
SELECT id, ts, device, value AS state, instance_id
FROM observations
WHERE type = 'Execution';

DROP VIEW IF EXISTS state_intervals;
CREATE VIEW state_intervals AS
SELECT device, state, ts AS start_ts,
       COALESCE(LEAD(ts) OVER w, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) AS end_ts,
       (julianday(COALESCE(LEAD(ts) OVER w, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))) - julianday(ts)) * 86400.0
           AS duration_seconds
FROM events
WINDOW w AS (PARTITION BY device ORDER BY ts, id);

-- Warnings/faults: one row per activation and per event that clears it (see mtc/conditions.py).
CREATE TABLE IF NOT EXISTS conditions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    device       TEXT NOT NULL,
    data_item_id TEXT NOT NULL,
    type         TEXT NOT NULL,           -- what it is about, e.g. 'SYSTEM', 'LOAD'
    level        TEXT NOT NULL,           -- WARNING, FAULT, NORMAL, UNAVAILABLE
    native_code  TEXT NOT NULL DEFAULT '',  -- the machine's own code; '' when none
    severity     TEXT,
    qualifier    TEXT,
    message      TEXT NOT NULL DEFAULT '',
    instance_id  TEXT
);
CREATE INDEX IF NOT EXISTS conditions_device_item_idx ON conditions (device, data_item_id, id);
CREATE INDEX IF NOT EXISTS conditions_device_ts_idx ON conditions (device, ts);

-- Each Warning/Fault is an interval that ends at the first later event that clears it (a NORMAL/UNAVAILABLE
-- with the same native code, or with no code, which clears the whole data item) or replaces it (a new
-- WARNING/FAULT with the same code). end_ts is NULL while the condition is still active.
DROP VIEW IF EXISTS active_conditions;
DROP VIEW IF EXISTS condition_intervals;
CREATE VIEW condition_intervals AS
SELECT a.id, a.device, a.data_item_id, a.type, a.native_code, a.level, a.severity, a.qualifier, a.message,
       a.ts AS start_ts,
       (SELECT c.ts FROM conditions c
        WHERE c.device = a.device AND c.data_item_id = a.data_item_id AND c.id > a.id
          AND ((c.level NOT IN ('WARNING', 'FAULT') AND (c.native_code = '' OR c.native_code = a.native_code))
               OR (c.level IN ('WARNING', 'FAULT') AND c.native_code = a.native_code))
        ORDER BY c.id LIMIT 1) AS end_ts
FROM conditions a
WHERE a.level IN ('WARNING', 'FAULT');

CREATE VIEW active_conditions AS
SELECT device, data_item_id, type, native_code, level, severity, qualifier, message, start_ts
FROM condition_intervals
WHERE end_ts IS NULL;
