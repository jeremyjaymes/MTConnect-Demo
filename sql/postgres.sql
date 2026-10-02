-- Idempotent. Applied by docker-compose (initdb.d) on first start and by the logger on every connect.

-- One row per change of a device's data item (Execution, Program, PartCount, ...).
CREATE TABLE IF NOT EXISTS observations (
    id           BIGSERIAL PRIMARY KEY,   -- insertion order; breaks ts ties (marker vs. new value)
    ts           TIMESTAMPTZ NOT NULL,    -- the observation's own timestamp from the agent
    device       TEXT NOT NULL,
    data_item_id TEXT NOT NULL,
    type         TEXT NOT NULL,           -- MTConnect element name, e.g. 'Execution'
    value        TEXT NOT NULL,
    instance_id  TEXT,                    -- agent instanceId when stored (NULL for UNAVAILABLE markers)
    sub_type     TEXT                     -- e.g. 'x:CUT'; same across agents even when data item ids differ
);
-- Databases created before sub_type existed:
ALTER TABLE observations ADD COLUMN IF NOT EXISTS sub_type TEXT;
CREATE INDEX IF NOT EXISTS observations_device_ts_idx ON observations (device, ts);
CREATE INDEX IF NOT EXISTS observations_device_item_ts_idx ON observations (device, data_item_id, ts);

-- One-time migration from the old single-purpose `events` table: copy its rows, keep it as a backup.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = current_schema() AND table_name = 'events' AND table_type = 'BASE TABLE') THEN
        INSERT INTO observations (ts, device, data_item_id, type, value, instance_id)
        SELECT ts, device, 'execution', 'Execution', state, instance_id FROM events ORDER BY id;
        ALTER TABLE events RENAME TO events_legacy;
    END IF;
END $$;

-- `events` is now a view of the machine's Execution state (the original schema), so reports and
-- dashboards written against it keep working.
CREATE OR REPLACE VIEW events AS
SELECT id, ts, device, value AS state, instance_id
FROM observations
WHERE type = 'Execution';

-- Each state lasts until the device's next event; the open interval ends at now().
CREATE OR REPLACE VIEW state_intervals AS
SELECT device,
       state,
       ts AS start_ts,
       COALESCE(LEAD(ts) OVER w, now()) AS end_ts,
       EXTRACT(EPOCH FROM COALESCE(LEAD(ts) OVER w, now()) - ts)::double precision AS duration_seconds
FROM events
WINDOW w AS (PARTITION BY device ORDER BY ts, id);

-- Warnings/faults: one row per activation and per event that clears it (see mtc/conditions.py).
CREATE TABLE IF NOT EXISTS conditions (
    id           BIGSERIAL PRIMARY KEY,
    ts           TIMESTAMPTZ NOT NULL,
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
CREATE OR REPLACE VIEW condition_intervals AS
SELECT a.id, a.device, a.data_item_id, a.type, a.native_code, a.level, a.severity, a.qualifier, a.message,
       a.ts AS start_ts,
       (SELECT c.ts FROM conditions c
        WHERE c.device = a.device AND c.data_item_id = a.data_item_id AND c.id > a.id
          AND ((c.level NOT IN ('WARNING', 'FAULT') AND (c.native_code = '' OR c.native_code = a.native_code))
               OR (c.level IN ('WARNING', 'FAULT') AND c.native_code = a.native_code))
        ORDER BY c.id LIMIT 1) AS end_ts
FROM conditions a
WHERE a.level IN ('WARNING', 'FAULT');

CREATE OR REPLACE VIEW active_conditions AS
SELECT device, data_item_id, type, native_code, level, severity, qualifier, message, start_ts
FROM condition_intervals
WHERE end_ts IS NULL;

-- Seconds the machine's own accumulated-time counters advanced in each hour (Mazak v1.4 agents report
-- sub types x:TOTAL = powered on, x:AUTO = running a program in auto, x:CUT = cutting; item ids differ per agent). The counters only ever
-- grow, so an hour's increase is its highest value minus the previous hour's highest; the first hour we have
-- uses its own lowest value instead. Postgres only: it feeds the Grafana dashboard.
-- (dropped first: CREATE OR REPLACE cannot rename a view's columns)
DROP VIEW IF EXISTS accumulated_time_hourly;
CREATE VIEW accumulated_time_hourly AS
WITH hourly AS (
    SELECT device, sub_type, date_trunc('hour', ts) AS hour,
           MIN(value::bigint) AS first_value, MAX(value::bigint) AS last_value
    FROM observations
    WHERE type = 'AccumulatedTime' AND sub_type IN ('x:TOTAL', 'x:AUTO', 'x:CUT') AND value ~ '^[0-9]+$'
    GROUP BY device, sub_type, date_trunc('hour', ts)
)
SELECT device, sub_type, hour,
       COALESCE(last_value - LAG(last_value) OVER (PARTITION BY device, sub_type ORDER BY hour),
                last_value - first_value)::double precision AS seconds
FROM hourly;
