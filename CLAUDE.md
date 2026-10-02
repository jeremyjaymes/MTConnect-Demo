# Project guidance: MTConnect machine monitoring

Collects MTConnect agent data into Postgres/SQLite and shows it in Grafana. See `README.md` for how to run it.

## Role
Work as a senior Python developer. The codebase is read and maintained by junior developers, so it should
also teach good practice. Prefer the clear solution over the clever one.

## Code style
- **Readable first.** Descriptive names, short functions that do one thing, early returns over deep nesting.
- **Small, modular, self-contained files.** One responsibility per module (e.g. parsing, storage, collecting,
  reporting, CLI). A module should be understandable without opening five others. Keep dependencies one-way
  (CLI -> collector/report -> storage/parsing -> time helpers); no circular imports.
- **Pure logic apart from I/O.** Parsing and math take plain values and return plain values (no network, no DB),
  so they are trivially testable. Network and database calls live in thin, separate layers.
- **Type hints** on all function signatures. Use `dataclass`es for records instead of loose tuples/dicts.
- **Docstrings** on every module and public function: what it does and why, not a line-by-line restatement.
  Comments explain *why* (a domain quirk, a tradeoff), never *what*.
- **Constants over magic values.** Name states, defaults and SQL fragments once, in one place.
- **Errors:** catch specific exceptions, never bare `except:`. Say what failed and with which device/URL.
  A bad poll must not crash the collector loop.
- **SQL:** parameterized queries only, never string-formatted values. Keep schema SQL in `sql/`.
- **No dependencies, frameworks or abstractions** beyond what the task needs. Stdlib + `requests` + `psycopg`.

## Domain rules (learned from the real data; do not regress)
- Parse MTConnect XML by local element name; namespaces vary by agent version.
- Collect with `/current` once, then `/sample?from=<nextSequence>` filtered by `path` (MTConnect client best practice:
  https://mtconnect.mintlify.app/). Track `instanceId`; resync via `/current` on restart or `OUT_OF_RANGE`.
- Event time is the observation's own `timestamp`, not the local clock. (Exception: UNAVAILABLE written after a
  network error has no observation, so it uses local time.)
- Write a row only when a value changes; on startup resume from the last stored value.
- Agent `instanceId` change means an agent restart: insert an `AGENT_RESTART` marker. Lost observations get
  `DATA_GAP`, failed polls `UNAVAILABLE`. The first value after any marker is stamped no earlier than the poll time.
- Identify items by `type` + `sub_type`, never by `dataItemId`: ids differ between agents (`cut_time` on one, `ctime` on
  another). Log low-rate state items only; axis feedrates, line numbers, positions and loads change thousands of times an hour.
- Conditions: a Warning/Fault is cleared by a Normal with the same `nativeCode` (no code clears the whole item). Store
  activations and clears only. After a resync, conditions the agent no longer lists were cleared while we weren't watching.
- Utilization = ACTIVE / (window minus UNAVAILABLE, AGENT_RESTART and DATA_GAP time).
- Verify against real agent output (`curl .../probe`, `/current`); never invent sample XML.

## Testing
- pytest. Every behavior change gets a test; the interval/utilization math must always be covered.
- Use hand-built inputs and the local fake agent in `tests/` rather than the network.
- Run `pytest` before saying work is done, and say clearly what was not verified.

## Workflow
- Narrowest change that solves the task; fix root causes. Flag destructive or broad schema changes first.
- Never expose or commit secrets. `.env` is never committed; new env vars go in `.env.example`.
- Update `README.md` when a change affects how someone runs or operates the project.
- Track work in the Linear project "MTConnect Machine Monitoring" (team Geneseesoftware, P-GEN-8).
