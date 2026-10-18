"""Progress lines, messages into a running executor, and opening a finished session."""

import json

import pytest

from conductor import cli, procs, talk
from conductor.app import actions, model
from conductor.app.model import Project


def send(env, text: str, name: str = "s.signal") -> None:
    env.signal(name, text)
    env.tick_until(lambda: not (env.control / "signals" / name).exists())


def bad(env, name: str = "s.signal") -> bool:
    return (env.control / "signals" / f"{name}.bad").is_file()


def report(env, name: str) -> str:
    return (env.control / "reports" / f"code__{name}.md").read_text(encoding="utf-8")


def test_progress_lines_become_events(env):
    env.queue("001_p", 'FAKE progress {"note": "reading", "eta": "20m"}\n'
                       'FAKE progress {"note": "half way", "eta": "10m", "done": 2, "remaining": 2}')
    env.tick_until(lambda: env.finished("code/001_p"))
    p1, p2 = env.events("progress")
    assert (p1.detail["seq"], p1.detail["note"], p1.detail["eta"]) == (1, "reading", "20m")
    assert (p2.detail["done"], p2.detail["remaining"]) == (2, 2)
    assert p2.id < env.finished("code/001_p").id
    # A restarted daemon logs nothing twice.
    env.d.log.close()
    env.d = env.new_daemon()
    env.d.tick()
    assert len(env.events("progress")) == 2


def test_a_message_reaches_a_busy_executor_and_changes_what_it_does(env):
    env.queue("001_busy", "FAKE until-message 30")
    env.tick_until(lambda: env.events("start"))
    send(env, "by: app\ntell: 001 stop and write the summary # now")
    [tell] = env.events("tell")
    assert (tell.item, tell.detail["text"], tell.detail["by"]) == ("code/001_busy", "stop and write the summary # now", "app")
    env.tick_until(lambda: env.finished("code/001_busy"))
    [got] = env.events("delivered")
    assert (got.item, got.detail["tell"], got.detail["via"]) == ("code/001_busy", tell.id, "hook")
    text = report(env, "001_busy")
    assert f"CONTEXT: MESSAGE #{tell.id} from Matt via app" in text and "stop and write the summary # now" in text
    # It answered through progress, and stopped well before its 30 seconds.
    [answer] = env.events("progress")
    assert answer.detail["note"] == "got it: stop and write the summary # now"
    assert got.id < answer.id < env.finished("code/001_busy").id


@pytest.mark.slow
def test_tell_refusals_and_undelivered(env):
    go = env.root / "go"
    env.queue("001_long", f"FAKE report done\nFAKE wait-for {go.as_posix()} 30")
    env.queue("002_q", "FAKE report done", stage="false")
    env.tick_until(lambda: env.events("start"))
    for n, text in enumerate(["tell: 002 hello",            # queued
                              "tell: 009 hello",            # unknown
                              "tell: nowhere/001 hello",    # not a repo
                              "tell: 001"]):                # no message
        send(env, text, f"t{n}")
        assert bad(env, f"t{n}"), text
    errors = [e.detail["error"] for e in env.events("invalid")]
    assert "has not started" in errors[0] and "no running item" in errors[1]
    # Sent after its last tool call: it exits without seeing it.
    env.tick_until(lambda: (env.root / "go.waiting").exists())
    send(env, "tell: 001 too late", "late")
    go.touch()                              # only now may it exit, its last tool call long past
    env.tick_until(lambda: env.finished("code/001_long"))
    warn = [e for e in env.events("warning") if "never delivered" in e.detail.get("note", "")]
    assert warn and warn[0].item == "code/001_long" and not env.events("delivered")
    assert not talk.pending(env.control, "code/001_long")
    send(env, "tell: 001 hello", "exited")
    assert bad(env, "exited") and "has exited" in env.events("invalid")[-1].detail["error"]
    env.tick_until(lambda: env.finished("code/002_q"))


def test_hook_prints_context_once(tmp_path):
    talk.post(tmp_path, "code/001_x", 7, "first", "planner")
    talk.post(tmp_path, "code/001_x", 9, "second", None)
    out = json.loads(talk.hook(tmp_path, "code/001_x"))["hookSpecificOutput"]
    assert out["hookEventName"] == "PostToolUse"
    ctx = out["additionalContext"]
    assert ctx.index("MESSAGE #7 from Matt via planner") < ctx.index("MESSAGE #9 from Matt,") and "second" in ctx
    assert talk.hook(tmp_path, "code/001_x") is None       # delivered once
    assert [m["id"] for m in talk.delivered(tmp_path)] == [7, 9]
    settings = json.loads(talk.hook_settings("py", tmp_path, "code/001_x"))
    [group] = settings["hooks"]["PostToolUse"]
    assert group["matcher"] == "" and group["hooks"][0]["args"][:3] == ["-m", "conductor.talk", "hook"]


def test_cli_tell_and_open(env, capsys, monkeypatch):
    env.queue("001_x", "FAKE until-message 30")
    env.tick_until(lambda: env.events("start"))
    assert cli.main(["tell", "001", "wrap", "up", "--control", str(env.control)]) == 0
    [sig] = (env.control / "signals").glob("cli-*.signal")
    assert sig.read_text(encoding="utf-8") == "by: cli\ntell: code/001_x wrap up\n"
    assert cli.main(["open", "001", "--control", str(env.control)]) == 1      # still running
    assert "would fork it" in capsys.readouterr().err
    env.tick_until(lambda: env.finished("code/001_x"))
    assert env.events("tell")[0].detail["by"] == "cli"
    assert cli.main(["tell", "001", "hi", "--control", str(env.control)]) == 1
    opened = []
    monkeypatch.setattr(procs, "open_console", lambda argv, cwd: opened.append((argv, cwd)))
    assert cli.main(["open", "code/001", "--control", str(env.control)]) == 0
    [(argv, cwd)] = opened
    sid = env.finished("code/001_x").detail["session_id"]
    assert argv[-2:] == ["--resume", sid]
    assert cwd == env.cfg.repos["code"]                    # merged: its worktree is gone


def test_open_session_prefers_the_worktree(env):
    wt = env.cfg.worktree("code", "003_red")
    wt.mkdir(parents=True)
    got = []
    cwd, argv = actions.open_session(env.cfg, "code/003_red", "sid-1", spawn=lambda a, c: got.append(c))
    assert cwd == wt == got[0] and argv[-2:] == ["--resume", "sid-1"]


def test_activity_shows_progress_and_messages(env):
    log = env.d.log
    log.append("progress", item="code/004_x", note="tests pass", eta="5:40pm", done=3, remaining=1)
    t = log.append("tell", item="code/004_x", text="ship it", by="app")
    log.append("delivered", item="code/004_x", tell=t.id, via="hook")
    p = Project(env.control)
    p.poll_events()
    shown = [e for e in p.filter_events(advanced=False) if e.item == "code/004_x"]
    assert [model.activity_text(e) for e in shown] == [
        "004_x: tests pass (ETA 5:40pm, 3 done, 1 to go)",
        f"message #{t.id} to 004_x (by app): ship it",
        f"message #{t.id} reached 004_x"]
    p.close()
