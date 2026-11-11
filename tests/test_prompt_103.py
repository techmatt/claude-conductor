"""103: load lines name conductor's own processes and other projects' items (aicomplete
170), a message closes the question it answered and long jobs get a progress line
(aicomplete 167), and test runs keep their temp folders under the repo."""

import io
import json
import os
from pathlib import Path
from types import SimpleNamespace

import psutil

from conductor import ask_server, inbox, load, questions
from test_memory import status
from test_preamble import SHORT, render


def flat(text: str) -> str:
    return " ".join(text.split())


# ------------------------------------------------------------------ load attribution
def test_conductor_command_lines_are_told_apart():
    venv = r"C:\Code\claude-conductor\.venv\Scripts\python.exe"
    assert load.conductor_cmd([venv, "-u", "-m", "conductor", "run", r"C:\Code\fractals-control", "--after", "1"]) \
        == Path(r"C:\Code\fractals-control")
    assert load.conductor_cmd([venv, "-m", "conductor", "app", r"C:\Code\a-control"]) is True
    assert load.conductor_cmd([venv, "-P", "-m", "conductor.remote.broker", r"C:\Code\a-control"]) is True
    assert load.conductor_cmd(["uv.exe", "run", "conductor", "run", "C:/x-control"]) == Path("C:/x-control")
    assert load.conductor_cmd([r"C:\bin\conductor.exe", "status"]) is True
    assert load.conductor_cmd([venv, "-m", "pytest", "-k", "conductor"]) is False
    assert load.conductor_cmd(["node.exe", "conductor.js"]) is False
    assert load.conductor_cmd([]) is False


READS: list[int] = []


class Proc(SimpleNamespace):
    def cmdline(self):
        READS.append(self.pid)
        if self.cmd is None:
            raise psutil.AccessDenied(self.pid)
        return self.cmd


def box(*rows):
    """`process_iter` over stand-ins: (pid, name, CPU seconds so far, command line)."""
    return lambda attrs: [Proc(pid=pid, cmd=cmd, info={"name": name, "create_time": 1.0,
                                                       "cpu_times": SimpleNamespace(user=cpu, system=0.0)})
                          for pid, name, cpu, cmd in rows]


def test_load_names_conductor_and_other_projects_items(tmp_path, monkeypatch):
    mine, theirs = tmp_path / "mine-control", tmp_path / "fractals-control"
    load.publish(theirs / ".conductor", "fractals", {41: "fractals/012_zoom"})
    load.publish(mine / ".conductor", "mine", {5: "code/001_x"})
    monkeypatch.setattr(psutil, "cpu_count", lambda: 10)
    monkeypatch.setattr(psutil, "cpu_percent", lambda interval: 70.0)
    py = "python.exe"
    rows = [(5, "claude.exe", 0.0, ["claude"]),
            (20, py, 0.0, [py, "-m", "conductor", "run", str(mine)]),
            (21, py, 0.0, [py, "-m", "conductor", "run", str(theirs)]),
            (22, py, 0.0, [py, "-m", "conductor.remote.broker", str(theirs)]),
            (41, "fractal-engine.exe", 0.0, ["fractal-engine.exe"]),
            (50, "MsMpEng.exe", 0.0, None)]                  # access denied: outside
    READS.clear()
    s = load.Sampler(mine)
    monkeypatch.setattr(psutil, "process_iter", box(*rows))
    assert s.sample({5: "code/001_x"}) is None
    s.at -= 10
    spent = {5: 30.0, 20: 2.0, 21: 1.0, 22: 1.0, 41: 20.0, 50: 6.0}
    monkeypatch.setattr(psutil, "process_iter", box(*[(p, n, spent[p], c) for p, n, _, c in rows]))
    got = s.sample({5: "code/001_x"})
    assert got["top"] == [{"who": "code/001_x", "cpu": 30, "item": True},
                          {"who": "fractals/012_zoom", "cpu": 20, "item": False, "of": "fractals"},
                          {"who": "MsMpEng.exe", "cpu": 6, "item": False},
                          {"who": "conductor", "cpu": 4, "item": False, "of": "conductor"}]
    assert load.text(got) == ("CPU 70%: code/001_x 30%, fractals/012_zoom 20% (fractals), "
                              "MsMpEng.exe 6% (outside), conductor 4%")
    assert sorted(READS) == [20, 21, 22, 41, 50]             # a command line is read once per process, never an item's
    assert not load.own_share(got, "fractals/012_zoom")      # another project's item is never told


def test_the_daemon_publishes_its_items_pids_at_each_sample(env):
    env.d.cfg.memory_sample_seconds = 0.01
    env.d.memory_probe = lambda: status(50, 10)
    env.d.sample_memory(force=True)
    data = json.loads((env.cfg.state_dir / load.ITEMS_FILE).read_text(encoding="utf-8"))
    assert data == {"project": "test", "pids": {}}           # nothing running yet
    assert env.d._load.control == env.control.resolve()


# ------------------------------------------------------------------ a message closes a question
def test_progress_answered_closes_the_open_question_a_message_answered(env):
    server = ask_server.Server(env.control, "code/009_q", 10, None, out=io.StringIO())
    q = questions.create(env.control, "code/009_q", "Which band?", [], "45-85", False, 10)
    text, err = server.progress({"note": "message #12 says 50-80; using it", "answered": q.id})
    assert not err and f"{q.id} is closed." in text
    q = questions.load(q.path)
    assert q.result["outcome"] == "by_message" and "50-80" in q.result["reason"]
    assert server.progress({"note": "again", "answered": q.id})[1]          # already resolved
    assert server.progress({"note": "x", "answered": "code__nope__1"})[1]   # not this item's
    env.d.tick()
    [ev] = env.events("question_closed")
    assert inbox.describe(ev).startswith(f"`{q.id}` closed: answered by a message")
    assert "answered" in ask_server.PROGRESS_TOOL["inputSchema"]["properties"]


def test_the_preambles_ask_for_a_progress_line_on_a_long_job_and_for_closing(env):
    full = flat(render(env, SHORT * 3))
    assert "as you launch any job expected to run over ~15 minutes (its expected end in the note)" in full
    assert 'A message that answers an open question closes it: `progress` with `answered: "<id>"`' in full
    light = flat(render(env, SHORT))
    assert "line at start and launching any job over ~15m (its end)" in light
    assert len(light.split()) <= 430


# ------------------------------------------------------------------ test temp beside the repo
def test_this_run_keeps_its_temp_folders_beside_the_repo_outside_its_tree(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    root = repo.parent / f"{repo.name}-pytest-tmp"          # 104: not inside the working tree
    assert root in tmp_path.resolve().parents and repo not in tmp_path.resolve().parents
    assert "pytest-of-" not in str(tmp_path)
    assert os.environ.get("PYTEST_XDIST_WORKER") is None or "popen-gw" in str(tmp_path)
