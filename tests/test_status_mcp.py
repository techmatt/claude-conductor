"""`conductor mcp`, the trial status server (docs/mcp.md), driven by tests/mcp_client.py."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from mcp_client import call_status  # noqa: E402

from conductor.util import stamp  # noqa: E402


def test_status_for_a_live_project_names_the_running_item_and_its_progress(env):
    env.queue("001_long", env.hold())
    env.queue("002_next", "FAKE ok")
    env.tick_until(lambda: env.events("start"))
    env.d.log.append("progress", item="code/001_long", note="halfway through the tiles", eta="5:40pm")
    # A test's daemon takes no lock; this process stands in as the live daemon.
    (env.cfg.state_dir / "daemon.json").write_text(
        json.dumps({"pid": os.getpid(), "started": stamp(), "beat": stamp()}), encoding="utf-8")
    text, is_error = call_status("test", [env.control])
    env.release()
    assert not is_error
    lines = text.splitlines()
    assert lines[0].startswith("test — daemon alive (pid ") and "heartbeat" in lines[0] and "last event #" in lines[0]
    assert lines[1] == "code:"
    assert lines[2].startswith("  running 001_long ")
    assert "progress" in lines[3] and "halfway through the tiles (ETA 5:40pm)" in lines[3]
    assert lines[4].startswith("  queued 002_next")
    assert "proposed: none" in text
    assert len(text) <= 1500


def test_status_says_plainly_a_daemon_is_down(env):
    (env.cfg.state_dir / "daemon.json").write_text(
        json.dumps({"pid": 999999, "started": stamp(), "beat": stamp()}), encoding="utf-8")
    text, is_error = call_status("test", [env.control])
    assert not is_error
    assert text.startswith("test — daemon DOWN: it died (pid 999999")
    assert "Nothing launches" in text
    listing, _ = call_status(None, [env.control])
    assert listing.startswith("test: daemon DOWN, 0 running, 0 queued")
    other, is_error = call_status("nope", [env.control])
    assert not is_error and other == "no project 'nope'; known: test"
