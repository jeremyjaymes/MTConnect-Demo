"""Command-line interface: `run` (collect) and `report` (utilization)."""
import argparse
import os
from typing import Optional, Sequence

from mtc import collector, report
from mtc.models import Source
from mtc.states import CONDITION, EXECUTION
from mtc.storage import DEFAULT_SQLITE_PATH, open_store
from mtc.timeutil import parse_ts

# Mazak's public demo agent (one agent per port, one device each): http://mtconnect.mazakcorp.com/
DEFAULT_AGENT_URL = "http://mtconnect.mazakcorp.com:5719"
DEFAULT_DEVICE = "HCN001"
DEFAULT_POLL_SECONDS = 2.0
# Low-rate items that say something about machine state. Names are MTConnect element names; an agent that
# doesn't have one simply never reports it. High-rate items (axis feedrates, line numbers, positions, loads)
# are left out on purpose: they change thousands of times an hour and say nothing about state.
DEFAULT_TRACKED = [
    EXECUTION, CONDITION,
    "ControllerMode", "EmergencyStop", "DoorState",
    "ToolNumber", "PartCount", "Program", "WorkOffset",
    "PathFeedrateOverride", "ControllerModeOverride",
    "EquipmentMode", "AccumulatedTime", "Temperature", "RotaryVelocity",
]


def add_common_options(parser: argparse.ArgumentParser, *, on_subcommand: bool, collecting: bool) -> None:
    """Add the shared flags so they work both before and after the subcommand.

    On a subcommand parser defaults are SUPPRESSed; otherwise an unset flag there would overwrite a value
    given before the subcommand (`--db x run` would silently lose x).
    """
    def default(value):
        return argparse.SUPPRESS if on_subcommand else value

    parser.add_argument("--device", action="append", default=default(None),
                        help=f"repeatable; run default {DEFAULT_DEVICE}, report default every device in the DB")
    parser.add_argument("--db", default=default(None), help=f"SQLite file (default {DEFAULT_SQLITE_PATH})")
    parser.add_argument("--db-url", default=default(None),
                        help="Postgres URL, e.g. postgresql://user:pass@localhost:5434/mtc "
                             "(default: env MTC_DB_URL, unless --db is given)")
    if collecting:
        parser.add_argument("--agent-url", default=default(DEFAULT_AGENT_URL))
        parser.add_argument("--poll-seconds", type=float, default=default(DEFAULT_POLL_SECONDS))
        parser.add_argument("--source", action="append", default=default(None), metavar="URL,DEVICE[,LABEL]",
                            help="a device on a different agent, repeatable, e.g. "
                                 "http://host:5611,Mazak,Mazak-5611. LABEL is the name stored in the database")
        parser.add_argument("--backfill", action="store_true", default=default(False),
                            help="on the first poll, also read the history the agent still buffers (hours of "
                                 "data), instead of starting from now")
        parser.add_argument("--track", action="append", default=default(None), metavar="ELEMENT",
                            help=f"what to log, repeatable (default: {len(DEFAULT_TRACKED)} state items, see DEFAULT_TRACKED in cli.py): an MTConnect "
                                 f"element such as Execution, Program, PartCount, or {CONDITION} for warnings/faults")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Log MTConnect machine data and report utilization.")
    add_common_options(parser, on_subcommand=False, collecting=True)
    subcommands = parser.add_subparsers(dest="command", required=True)

    run_parser = subcommands.add_parser("run", help="poll the agent and log changes")
    add_common_options(run_parser, on_subcommand=True, collecting=True)

    report_parser = subcommands.add_parser("report", help="utilization report")
    add_common_options(report_parser, on_subcommand=True, collecting=False)
    report_parser.add_argument("--since", help="ISO timestamp")
    report_parser.add_argument("--until", help="ISO timestamp")
    return parser


def build_sources(args: argparse.Namespace, parser: argparse.ArgumentParser) -> list[Source]:
    """Combine --source entries with --device names (which use --agent-url)."""
    sources = []
    for text in args.source or []:
        parts = [part.strip() for part in text.split(",")]
        if len(parts) not in (2, 3) or not all(parts):
            parser.error(f"--source must be URL,DEVICE or URL,DEVICE,LABEL (got {text!r})")
        sources.append(Source(*parts))
    if args.device or not sources:
        sources += [Source(args.agent_url, device) for device in args.device or [DEFAULT_DEVICE]]
    return sources


def resolve_database(args: argparse.Namespace, parser: argparse.ArgumentParser) -> tuple[Optional[str], str]:
    """Return (postgres_url, sqlite_path). An explicit CLI flag always beats the MTC_DB_URL env var."""
    if args.db and args.db_url:
        parser.error("use either --db or --db-url, not both")
    if args.db:
        return None, args.db
    db_url = args.db_url or os.environ.get("MTC_DB_URL")
    return db_url, DEFAULT_SQLITE_PATH


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    db_url, sqlite_path = resolve_database(args, parser)
    store = open_store(db_url, sqlite_path)

    try:
        if args.command == "run":
            collector.run(store, build_sources(args, parser), args.track or DEFAULT_TRACKED,
                          args.poll_seconds, args.backfill)
        else:
            report.print_report(store, args.device or [],
                                parse_ts(args.since) if args.since else None,
                                parse_ts(args.until) if args.until else None)
            store.close()
    except KeyboardInterrupt:
        pass
