# Development

Conventions for changing MTConnect-Demo. How to run it is in `README.md`.

## Layout

Dependencies point one way: the CLI wires the collector and the report, those call storage and parsing, and parsing and the interval math do not call the network or the database. One module, one job. A file should be readable without opening five others.

## Style

- Prefer a clear solution. Descriptive names, short functions, early returns.
- Type hints on every function. Use dataclasses for records.
- Docstrings on modules and public functions say what the code is for. Comments say why: a domain quirk or a tradeoff.
- Name states, defaults, and SQL fragments once.
- Catch specific exceptions. A bad poll must not crash the collector loop, and the message should say which device or URL failed.
- Parameterized SQL only. Schema files live in `sql/`.
- Dependencies stay at the standard library, `requests`, and `psycopg`.

## Behavior that must not regress

These came from real agent responses. Tests cover them.

- Parse MTConnect XML by local element name. Namespaces differ by agent version.
- Collect with `/current` once, then `/sample?from=<nextSequence>` filtered by `path` ([client pattern](https://mtconnect.mintlify.app/)). Track `instanceId`. On restart or `OUT_OF_RANGE`, resync with `/current`.
- Store the observation's own `timestamp`. A network error has no observation, so `UNAVAILABLE` uses local time. The first value after any marker is stamped no earlier than the poll time.
- Write a row only when a value changes. On startup, resume from the last stored value.
- An `instanceId` change is an agent restart (`AGENT_RESTART`). Lost observations are `DATA_GAP`. Failed polls are `UNAVAILABLE`.
- Identify items by `type` and `sub_type`, not `dataItemId`. Ids differ between agents (`cut_time` on one, `ctime` on another). Log low-rate state items. Leave out axis feedrates, line numbers, positions, and loads.
- A Warning or Fault clears when a Normal arrives with the same `nativeCode`. A Normal with no code clears the whole item. Store activations and clears only. After a resync, conditions the agent no longer lists were cleared while the collector was not watching.
- Utilization = ACTIVE / (window minus UNAVAILABLE, AGENT_RESTART, and DATA_GAP time).
- Check behavior against real agent output (`/probe`, `/current`) or the fake agent in `tests/`. Do not invent sample XML.

## Tests

`pytest`. Keep the interval and utilization math covered. Use hand-built inputs and the local fake agent in `tests/`, not the network. A behavior change gets a test.

The same tests can run against Postgres: `MTC_TEST_PG_URL=postgresql://... pytest`. That run only touches device `X`.

## Changes

Keep the change as small as the fix. Call out a destructive or broad schema change before doing it. Never commit secrets. `.env` stays untracked; new environment variables go in `.env.example`. Update `README.md` when a change affects how someone runs or operates the project.
