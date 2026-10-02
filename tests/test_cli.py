"""The --db flag must work before or after the subcommand, and beat MTC_DB_URL."""
import os
import signal
import sqlite3
import subprocess
import sys
import time

import pytest

from tests.conftest import iso


@pytest.mark.parametrize("argv", [["--db", "{db}", "run"], ["run", "--db", "{db}"]])
def test_db_flag_both_positions_multi_device(agent, tmp_path, argv):
    agent.publish("ACTIVE", iso(0))
    db = tmp_path / "cli.db"
    cmd = [sys.executable, "mtc_logger.py", *[a.format(db=db) for a in argv], "--agent-url", agent.url,
           "--device", "X", "--device", "Y", "--poll-seconds", "0.1"]
    # MTC_DB_URL points nowhere: an explicit --db must win over it
    env = {**os.environ, "MTC_DB_URL": "postgresql://nobody@127.0.0.1:1/none"}
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    proc.send_signal(signal.SIGINT)
    proc.wait(timeout=5)
    assert {r[0] for r in sqlite3.connect(db).execute("SELECT device FROM observations")} == {"X", "Y"}


def test_source_option_parsing():
    import argparse

    from mtc.cli import build_parser, build_sources

    parser = build_parser()
    args = parser.parse_args(["run", "--source", "http://a:1,Dev", "--source", "http://b:2,Dev,Dev-b"])
    sources = build_sources(args, parser)
    assert [(s.agent_url, s.device, s.name) for s in sources] == \
        [("http://a:1", "Dev", "Dev"), ("http://b:2", "Dev", "Dev-b")]

    with pytest.raises(SystemExit):
        build_sources(parser.parse_args(["run", "--source", "just-a-url"]), parser)
