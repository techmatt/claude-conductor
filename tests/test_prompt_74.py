"""Prompt 74: the full preamble's premises, `~/.claude` and report-budget lines, one
*Asking and scope* section, and `wait_for` naming who runs beside the item when that changed."""

import io
import threading

from conductor import ask_server
from test_lean import record
from test_preamble import SHORT, render


def test_the_full_preamble_carries_the_new_lines(env):
    full = " ".join(render(env, SHORT * 3).split())
    assert "Asking and scope." in full and "Scope." not in full.replace("Asking and scope.", "")
    assert full.count("default overrides prompt") == 1 and full.count("`design: true`") == 1
    assert "Premises." in full and "re-read it from source" in full and "report only ratios" in full
    assert "config only (settings, permissions), never memory or a `CLAUDE.md`;" in full
    assert "`carry:` for the repo's `CLAUDE.md`" in full
    assert "the latest `progress` or `wait_for` return names who does" in full
    assert "in under ~60 lines; long tables go in the item's results folder, linked" in full
    assert "edit `~/.claude`\nconfig when it helps" not in full
    light = " ".join(render(env, SHORT).split())
    assert "`~/.claude`: config only, never memory" in light


def test_wait_for_says_when_what_runs_beside_changed(env, tmp_path):
    record(env, "001_a", phase="exec", running_beside=[])
    (tmp_path / "there").write_text("x", encoding="utf-8")
    server = ask_server.Server(env.control, "code/001_a", 10, None, out=io.StringIO(), workdir=tmp_path)
    text, _ = server.wait_for({"path": "there", "every": 0.05}, threading.Event())
    assert "Running beside" not in text
    record(env, "002_b", phase="exec")
    text, _ = server.wait_for({"path": "there", "every": 0.05}, threading.Event())
    assert text.endswith(" Running beside you now: code/002_b.")
    assert "Running beside" not in server.progress({"note": "same"})[0]      # said once, by either tool
