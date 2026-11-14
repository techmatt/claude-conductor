"""116: a long-lived browser worker survives bundle changes and a Wasm out-of-memory, and a
failed gate says why (aicomplete 2026-11-13: a 30-hour tab failed every gate with
`load failed: RangeError ... Out of memory` and idled, its line saying only "failed")."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from conductor.remote.tests.test_remote_board import board, job

PAGE = Path(__file__).resolve().parents[1] / "page"
HARNESS = Path(__file__).with_name("page_harness.mjs")
OOM = "load failed: RangeError: WebAssembly.instantiate(): Out of memory: Cannot allocate Wasm memory for new instance"

needs_node = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


def harness(tmp_path: Path, scenario: str) -> dict:
    out = subprocess.run(["node", str(HARNESS), str(PAGE / "worker.js"), str(tmp_path), scenario],
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_cores_left_running_across_twelve_bundles_keep_passing_the_gate(tmp_path):
    # A fake bundle's init() throws the browser's out-of-memory once a core has loaded two.
    d = harness(tmp_path, "bundles")
    assert len(d["gates"]) == 12 and all(g["pass"] for g in d["gates"]), d["gates"]
    assert len(d["results"]) == 72 and all(r["ok"] for r in d["results"])
    assert d["recycles"] and all(r["why"].startswith("a new bundle") for r in d["recycles"]) and not d["ooms"]


@needs_node
def test_an_out_of_memory_is_retried_in_a_fresh_core_without_a_reload(tmp_path):
    d = harness(tmp_path, "oom-once")
    assert [g["pass"] for g in d["gates"]] == [True] and all(r["ok"] for r in d["results"])
    # Cores run in parallel: two may meet the one forced failure before its marker lands.
    assert d["recycles"] and all(r["why"] == "out of memory" and r["oom"] for r in d["recycles"])
    assert not d["ooms"]


@needs_node
def test_a_fresh_core_out_of_memory_too_asks_the_page_to_reload_and_the_gate_says_why(tmp_path):
    d = harness(tmp_path, "oom-always")
    assert d["gates"][0]["value"].startswith("load failed: RangeError") and not d["gates"][0]["pass"]
    assert d["ooms"] and "Out of memory" in d["ooms"][0]["text"]


def test_the_page_replaces_a_recycled_core_and_reloads_once_on_out_of_memory():
    page = (PAGE / "index.html").read_text(encoding="utf-8")
    assert 'm.type === "recycle"' in page and "startCore(m.slot, m.task)" in page
    assert 'm.type === "oom"' in page and "OOM_RELOAD_MS" in page and 'store(sessionStorage, "oomReload"' in page
    assert "coresReplaced" in page


def test_a_failed_gate_says_why_in_the_view_the_state_and_the_now_line(tmp_path):
    b, _ = board(tmp_path)
    job(b)
    b.hello("lap", "local-remote", label="Work laptop")      # a browser would wait on a wasm build here
    assert b.poll("lap")["task"] == "gate"
    assert not b.gate("lap", "k1", OOM)
    view = b.view("lap")
    assert view["gate"]["state"] == "fail" and view["gate"]["why"].startswith("load failed: RangeError")
    assert "failed the fingerprint gate for k1: load failed: RangeError" in b.state_of("lap")["detail"]
    line = b.now_line()
    assert "1 failed the fingerprint gate (Work laptop: load failed: RangeError" in line
    b.hello("liar", "local-remote")
    assert b.poll("liar")["task"] == "gate"
    assert not b.gate("liar", "k1", "nope")
    assert "liar: wrong fingerprint: got nope, expected fp" in b.now_line()
