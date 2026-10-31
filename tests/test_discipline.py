"""Executor discipline (61): `wait_for`, the machine-load line, ETA drift, a healthy run left
unflagged, and a question whose default overrides the prompt."""

import subprocess
import sys
import threading
import time
from datetime import timedelta
from types import SimpleNamespace

import psutil
import pytest

from conductor import ask_server, daemon_memory, inbox, load, memory, procs, questions, state, talk
from conductor.app.model import Project
from conductor.items import Item
from conductor.util import now, read_text
from test_memory import status
from test_preamble import SHORT, render


def waiter(env, tmp_path) -> ask_server.Server:
    return ask_server.Server(env.control, "code/009_x", 10, None, out=None, workdir=tmp_path)


def wait(server, **args) -> tuple[str, bool]:
    return server.wait_for({"every": 0.05, **args}, threading.Event())


def test_wait_for_refuses_what_it_cannot_wait_on(env, tmp_path):
    s = waiter(env, tmp_path)
    for args in ({}, {"path": "a", "pid": 1}, {"pid": True}, {"pid": "12"}, {"log": "a"},
                 {"log": "a", "line": "("}, {"path": "a", "minutes": 0}, {"path": "a", "every": "soon"}):
        text, is_error = s.wait_for(args, threading.Event())
        assert is_error, (args, text)


@pytest.mark.slow
def test_wait_for_a_path_a_pid_and_a_log_line(env, tmp_path):
    s = waiter(env, tmp_path)
    threading.Timer(0.3, lambda: (tmp_path / "marker").touch()).start()
    assert wait(s, path="marker")[0].startswith(f"{tmp_path / 'marker'} exists")      # relative to the workdir

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.5)"])
    text, _ = wait(s, pid=child.pid)
    assert f"pid {child.pid} has exited" in text
    child.wait()
    assert "has exited" in wait(s, pid=child.pid)[0]        # already gone: at once

    log = tmp_path / "run.log"
    log.write_text("EXIT 0 early\nstep 1\n", encoding="utf-8")
    assert "has the line: EXIT 0 early" in wait(s, log="run.log", line=r"^EXIT \d")[0]    # a line already there counts
    assert "last line: step 1" in wait(s, log="run.log", line="never", minutes=0.01)[0]

    def more():
        with open(log, "a", encoding="utf-8") as fh:
            fh.write("all pass")                            # no newline yet
    threading.Timer(0.3, more).start()
    assert "has the line: all pass" in wait(s, log="run.log", line="all pass", minutes=0.2)[0]

    log.write_bytes("done\n".encode("utf-16"))              # a PowerShell redirect's log
    assert "has the line: done" in wait(s, log="run.log", line="^.?done", minutes=0.01)[0]
    assert "does not exist yet" in wait(s, path="nope", minutes=0.01)[0]
    assert "has nothing yet" in wait(s, log="nope.log", line="x", minutes=0.01)[0]


def test_wait_for_checks_each_every_but_a_message_at_once(env, tmp_path):
    s = waiter(env, tmp_path)
    (tmp_path / "late").unlink(missing_ok=True)
    threading.Timer(0.2, lambda: talk.post(env.control, "code/009_x", 1, "STOP", "cli")).start()
    started = time.time()
    text, is_error = s.wait_for({"path": "late", "every": 60}, threading.Event())
    assert time.time() - started < 5 and not is_error
    assert "A message from conductor is waiting" in text and "late does not exist yet" in text
    # The same mail cuts a wait short once: a helper's call must not spin on it.
    text, _ = s.wait_for({"path": "late", "every": 0.05, "minutes": 0.01}, threading.Event())
    assert text.startswith("Nothing yet")


def test_wait_for_cancelled_sends_nothing(env, tmp_path):
    cancel = threading.Event()
    cancel.set()
    assert waiter(env, tmp_path).wait_for({"path": "x"}, cancel) == (None, False)


def test_a_real_item_waits_and_gets_its_message(env):
    """Through the executor's own --mcp-config and hook: a relative path is the worktree's,
    and a tell ends the wait and is delivered as it returns."""
    env.queue("001_wait", 'FAKE wait-for-tool {"path": "made.txt", "every": 0.1, "minutes": 1}')
    item = "code/001_wait"
    env.tick_until(lambda: env.events("start"))
    env.signal("tell", f"tell: {item} look now")
    env.tick_until(lambda: env.finished(item))
    report = read_text(env.control / "reports" / "code__001_wait.md")
    assert "A message from conductor is waiting" in report and "CONTEXT: " in report and "look now" in report
    assert env.events("delivered")


def box(*rows):
    """`process_iter` over stand-in processes: (pid, name, CPU seconds so far)."""
    return lambda attrs: [SimpleNamespace(pid=pid, info={"name": name, "create_time": 1.0,
                                                         "cpu_times": SimpleNamespace(user=cpu, system=0.0)})
                          for pid, name, cpu in rows]


def test_load_names_the_item_and_what_is_outside(monkeypatch):
    monkeypatch.setattr(psutil, "cpu_count", lambda: 10)
    monkeypatch.setattr(psutil, "cpu_percent", lambda interval: 63.0)
    idle = (0, "System Idle Process", 9000.0)
    s = load.Sampler()
    monkeypatch.setattr(psutil, "process_iter", box(idle, (5, "node.exe", 10.0), (6, "python.exe", 20.0), (7, "python.exe", 1.0)))
    assert s.sample({}) is None                             # a rate takes two scans
    s.at -= 10                                              # ten seconds on a ten-thread box: 1s of CPU is 1%
    monkeypatch.setattr(psutil, "process_iter", box((0, "System Idle Process", 9100.0), (5, "node.exe", 30.0), (6, "python.exe", 25.0),
                                                    (7, "python.exe", 1.2), (8, "pytest.exe", 40.0)))    # 8 is new: all its time counts
    got = s.sample({5: "code/001_x", 6: "code/001_x"})
    assert got == {"cpu": 63, "top": [{"who": "pytest.exe", "cpu": 40, "item": False},
                                      {"who": "code/001_x", "cpu": 25, "item": True}]}      # 7's 0.2% is not named
    assert load.text(got) == "CPU 63%: pytest.exe 40% (outside), code/001_x 25%"
    assert s.sample({}) == got                              # too soon: the last one again


def test_load_reaches_now_status_and_the_memory_event(env, capsys):
    cfg = env.d.cfg
    cfg.memory_sample_seconds = 0.01
    cfg.memory_pressure_percent, cfg.memory_critical_percent = 85, 93
    box = status(50, 10)
    env.d.memory_probe = lambda: box
    busy = {"cpu": 71, "top": [{"who": "code/001_x", "cpu": 40, "item": True}, {"who": "pytest.exe", "cpu": 20, "item": False}]}
    env.d._load.sample = lambda owned: busy
    env.d.sample_memory(force=True)
    assert memory.load_line(cfg) == "load CPU 71%: code/001_x 40%, pytest.exe 20% (outside)"
    box = status(90, 10)
    env.d.memory_probe = lambda: box
    env.d.sample_memory(force=True)
    [ev] = env.events("memory")
    assert ev.detail["load"] == busy
    assert "CPU 71%: code/001_x 40%" in inbox.describe(ev)
    text = inbox.render(cfg, env.d.log, state.scan(cfg, env.d.log))
    now_section = text.split("## Now", 1)[1]
    assert "- load CPU 71%: code/001_x 40%, pytest.exe 20% (outside)" in now_section
    # No fresh sample, no line: a stopped daemon's load is not the box's.
    cfg.memory_sample_seconds = 0
    assert memory.load_line(cfg) is None


def test_load_sample_survives_a_daemon_with_no_load_yet(env):
    env.d.cfg.memory_sample_seconds = 0.01
    env.d.memory_probe = lambda: status(50, 10)
    env.d.sample_memory(force=True)                         # the first scan has no rate
    rec = memory.latest(env.cfg.state_dir)
    assert "load" not in rec and memory.load_line(env.d.cfg) is None


def progress(env, **detail):
    env.d.log.append("progress", item="code/001_x", **detail)


def at(minutes: float) -> str:
    return (now() + timedelta(minutes=minutes)).strftime("%I:%M%p").lstrip("0").lower()


def rows(*etas, done=0):
    return [{"title": f"s{n}", "eta": e, "done": n < done} for n, e in enumerate(etas)]


def test_eta_drift(env):
    log = env.d.log
    item = "code/001_x"
    assert inbox.eta_drift(log, item) is None
    progress(env, note="start", eta=at(120))
    assert inbox.eta_drift(log, item) is None               # no outline: nothing to disagree with
    progress(env, outline=rows(at(30), at(118)))
    assert inbox.eta_drift(log, item) is None               # the outline finishes first: fine
    progress(env, outline=rows(at(30), at(180)))
    assert inbox.eta_drift(log, item) == f"ETA {at(120)} is earlier than the outline's finish {at(180)}"
    assert "**ETA " in inbox.now_progress(log, item)
    progress(env, outline=rows(at(30), at(180), done=2))
    assert inbox.eta_drift(log, item) is None               # every row done: nothing left to drift
    progress(env, outline=rows(at(30), at(121)))
    assert inbox.eta_drift(log, item) is None               # within the slack
    # Already past while rows remain. A duration from a posting long gone reads as past.
    log.append("progress", item="code/002_y", note="x", eta="0m")
    log.append("progress", item="code/002_y", outline=rows(at(60), at(90), done=1))
    time.sleep(0.01)
    assert inbox.eta_drift(log, "code/002_y") == "ETA 0m has passed with 1 outline row(s) left"


def test_eta_drift_on_the_apps_progress_line(env):
    env.queue("001_x", f"FAKE sleep 0\n{env.hold()}")
    item = "code/001_x"
    env.tick_until(lambda: env.events("start"))
    env.d.log.append("progress", item=item, outline=rows(at(60)))
    env.d.log.append("progress", item=item, note="going", eta="0m")
    time.sleep(0.01)
    [activity] = next(r for r in Project(env.control).snapshot().repos if r.name == "code").running
    assert "going" in activity.progress and "has passed with 1 outline row(s) left" in activity.progress
    env.release()
    env.tick_until(lambda: env.finished(item))


@pytest.mark.slow
@pytest.mark.parametrize("line, flagged", [("FAKE writer out.log 30", False), ("FAKE hog 5 30", True)])
def test_quiet_is_not_flagged_while_a_process_of_its_own_writes(env, monkeypatch, line, flagged):
    cfg = env.d.cfg
    cfg.memory_sample_seconds, cfg.watch_check_minutes, cfg.watch_quiet_minutes = 0.05, 0.001, 1000
    if flagged:                                             # else a slow box's tick gap outlasts the window
        monkeypatch.setattr(daemon_memory, "WRITING_SECONDS", 0.5)
    env.d._launch_watch = lambda *a, **k: "not in this test"
    env.queue("001_long", f"{line}\n{env.hold()}")
    item = "code/001_long"
    env.tick_until(lambda: env.d._writes.get(item) and (flagged or env.d.still_writing(item)))
    time.sleep(0.6 if flagged else 0)                       # the idle child's start is behind it
    env.d.tick()
    assert env.d.still_writing(item) is not flagged
    cfg.watch_quiet_minutes = 0.0001
    env.d._watch_at = None
    env.d.tick()
    assert bool([e for e in env.events("watch_flag") if e.detail["flag"] == "quiet"]) is flagged
    env.release()
    env.tick_until(lambda: env.finished(item))


def test_writes_leave_out_the_executor_and_conductors_own_processes():
    server = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)", "-m", "conductor.ask_server"],
                              stdout=subprocess.DEVNULL)
    try:
        p = psutil.Process(server.pid)
        assert memory._written(p, "claude") == memory._written(p, "shell") == memory._written(p, "chromium") == 0
        assert memory._written(p, "python") == 0        # its command line names conductor's
        assert memory._written(p, "node") >= 0
    finally:
        server.kill()
        server.wait()


def test_question_flagging_a_default_over_the_prompt(env):
    q = questions.create(env.control, "code/001_x", "default overrides prompt: skip the sweep\nRun it anyway?", [],
                         "run it", False, 0.001)
    assert q.overrides == "skip the sweep"
    plain = questions.create(env.control, "code/001_x", "Keep the default overrides prompt: line? No.", [], "no", False, 10)
    assert plain.overrides is None
    blocking = questions.create(env.control, "code/001_x", "default overrides prompt: x\nWhat now?", [], None, True, 10)
    assert blocking.overrides is None                        # no default to override with
    env.d.process_questions()
    ask = next(e for e in env.events("ask") if e.detail["question"] == q.id)
    assert ask.detail["overrides_prompt"] == "skip the sweep"
    assert "**default overrides prompt: skip the sweep**" in inbox.describe(ask)
    text = inbox.render(env.d.cfg, env.d.log, state.scan(env.d.cfg, env.d.log))
    assert f"`{q.id}` from code/001_x — default at" in text and "**default overrides prompt: skip the sweep**" in text
    questions.resolve(q, "default", q.default, "server")
    env.d.process_questions()
    [taken] = env.events("default")
    assert "nobody answered; **default overrides prompt: skip the sweep**" in inbox.describe(taken)
    from conductor.notify import message
    assert "default overrides prompt" in message("p", "ask", "code/001_x", "code", ask.detail)[1]


def test_answer_receipt_marks_a_flagged_question(env):
    q = questions.create(env.control, "code/001_x", "default overrides prompt: skip it\nRun it?", [], "run it", False, 10)
    env.d.tick()
    questions.write_answer(q, "no, skip")
    questions.resolve(q, "answered", "no, skip", "server")
    env.tick_until(lambda: env.events("answer"))
    env.tick_until(lambda: (env.control / "receipts" / "questions" / f"{q.id}.answer.md").is_file())
    assert "its default overrode the prompt: skip it" in read_text(env.control / "receipts" / "questions" / f"{q.id}.answer.md")


FULL_LINES = ("A skip in the prompt binds", "default overrides prompt", "`out/`", "%TEMP%", "below-normal priority",
              "4 hardware threads", "at most 2\nheavy subagents", "halve thread counts", "`~/.claude`", "over an hour of the time limit",
              "mcp__conductor__wait_for")
LIGHT_LINES = ("A skip in the prompt", "default overrides prompt", "`out/`", "%TEMP%", "below-normal priority", "4 hardware threads",
               "at most 2 heavy", "half the threads", "`~/.claude`", "over an hour left", "mcp__conductor__wait_for")


def test_both_preambles_carry_the_rules(env):
    full = render(env, SHORT * 3)
    assert "Outline." in full and all(s in full for s in FULL_LINES), [s for s in FULL_LINES if s not in full]
    light = render(env, SHORT)
    assert "Outline." not in light and all(s in light for s in LIGHT_LINES), [s for s in LIGHT_LINES if s not in light]


def test_templates_carry_the_leftover_line_but_a_watch(env):
    from conductor import executor
    def body(template):
        return executor.body(env.d.cfg, Item("code", "001_a", env.control / "x.md", num=1, body="", template=template))
    for template in ("bug_hunt", "self_audit", "general_cleanup", "disk_cleanup"):
        assert "Leftover budget." in body(template)
    assert "Leftover budget." not in body("watch")


def test_the_mcp_config_names_the_workdir(env):
    from conductor import executor
    item = Item("code", "001_a", env.control / "x.md", num=1, body="")
    assert '"--workdir"' in executor.mcp_config(env.d.cfg, item, "sid", env.root / "wt")


def test_create_time_of_a_gone_process():
    assert procs.create_time(2 ** 22 + 3) is None
