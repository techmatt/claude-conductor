"""Progress lines, messages into a running executor, and opening a finished session."""

import io
import json

import pytest

from conductor import cli, procs, talk
from conductor.app import actions, model
from conductor.app.model import Project
from conductor.inbox import describe


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
    # The first progress line after the delivery is its reply.
    assert (got.detail["after_seq"], answer.detail["reply_to"]) == (0, tell.id)
    assert f"(reply to #{tell.id})" in describe(answer) and f"(reply to #{tell.id})" in model.activity_text(answer)


@pytest.mark.slow
def test_conversation_ties_replies_to_their_tells(env):
    go = env.root / "go"
    env.queue("001_talk", 'FAKE progress {"note": "reading", "eta": "5m"}\n'
                          "FAKE until-message 30\n"
                          f"FAKE wait-for {go.as_posix()} 30\n"
                          'FAKE progress {"note": "noted"}\n'
                          'FAKE progress {"note": "amendment applied"}\n'
                          'FAKE ask {"question": "Which colour?", "options": ["red", "blue"], "default": "blue"}')
    env.tick_until(lambda: env.events("progress"))
    send(env, "by: app\ntell: 001 how is it going")
    [tell] = env.events("tell")
    env.tick_until(lambda: (env.root / "go.waiting").exists())
    env.d.tick()                            # the reply logged before the amendment goes in
    actions.amend(env.control, "code/001_talk", "also add a test")
    env.tick_until(lambda: env.events("amended"))
    [am] = env.events("amended")
    go.touch()
    env.tick_until(lambda: env.finished("code/001_talk"))
    # The amendment came with the hook after the first progress call (`progress` hands out
    # no mail): that line was written before it was seen, so the reply is the next one.
    assert env.d.log.last("delivered", item="code/001_talk").detail["via"] == "hook"
    by_note = {e.detail["note"]: e for e in env.events("progress")}
    assert "reply_to" not in by_note["reading"].detail and "reply_to" not in by_note["noted"].detail
    assert by_note["got it: how is it going"].detail["reply_to"] == tell.id
    assert by_note["amendment applied"].detail["reply_to"] == am.id
    project = Project(env.control)
    project.poll_events()
    turns = model.conversation(project.trail("code/001_talk").events)
    assert [t.title.split(" #")[0].split(",")[0] for t in turns] == [
        "Started", "Progress", "Message", "Amendment", "Progress", "Question (default 'blue' at "
        + env.events("ask")[0].detail["deadline_clock"] + ")", "Finished done"]
    msg = next(t for t in turns if t.id == tell.id)
    assert ("reply", by_note["got it: how is it going"].ts, "got it: how is it going") in msg.under and not msg.note
    amend = next(t for t in turns if t.id == am.id)
    assert amend.under[0] == ("delivery", "", "delivered after its next tool call")
    assert amend.under[-1][0] == "reply" and amend.under[-1][2] == "amendment applied"
    q = next(t for t in turns if t.title.startswith("Question"))
    assert q.under[0][0] == "default taken (nobody answered)" and q.under[0][2] == "blue"
    fin = turns[-1]
    assert fin.report and "slow: none" in fin.text and fin.tone == "done"
    # Plumbing stays out; a restarted daemon tags nothing twice.
    assert not any(t.title.startswith(("delivered", "merge", "enqueue")) for t in turns)
    env.d.log.close()
    env.d = env.new_daemon()
    env.d.tick()
    assert env.d.awaiting_reply == {}


def test_conversation_waits_and_warns():
    from conductor.events import Event
    ev = lambda i, kind, **d: Event(i, f"2026-10-20T10:0{i}:00-07:00", kind, "code/001_x", "code", [], d)
    turns = model.conversation([
        ev(1, "start", session_id="s"),
        ev(2, "tell", text="first", by="app"),
        ev(3, "delivered", tell=2, via="hook", after_seq=0),
        ev(4, "tell", text="second", by="planner"),
        ev(5, "tell", text="late", by="app"),
        ev(6, "warning", tell=5, note="message #5 was never delivered: the executor exited first"),
        ev(7, "progress", seq=1, note="both", reply_to=[2, 99]),
    ])
    first, second, late, both = turns[1:]
    assert first.under[-1][:1] == ("reply",) and not first.note
    assert (second.note, second.tone) == ("waiting for delivery, after its next tool call", "wait")
    assert late.tone == "warn" and "never delivered" in late.note
    # A reply to a tell not in this trail still shows on its own.
    assert both.title == "Progress (reply to #2, #99)"


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
    assert "Matt, the operator, writing through conductor's message channel" in ctx
    assert "pass it word for word straight up to the lead" in ctx
    settings = json.loads(talk.hook_settings("py", tmp_path, "code/001_x"))
    [group] = settings["hooks"]["PostToolUse"]
    assert group["matcher"] == "" and group["hooks"][0]["args"][:3] == ["-m", "conductor.talk", "hook"]
    assert "PreToolUse" not in settings


def test_only_the_lead_takes_mail(tmp_path, monkeypatch, capsys):
    """A subagent's call (Claude Code's `agent_id`) leaves the mail waiting; the lead's
    next call, or one whose input carries no readable marker, takes it."""
    talk.post(tmp_path, "code/001_x", 7, "first", "app")
    sub = {"hook_event_name": "PostToolUse", "tool_name": "Read", "agent_id": "a1b2", "agent_type": "Explore"}
    assert talk.subagent_call(sub) and not talk.subagent_call({"agent_id": ""})
    assert not talk.subagent_call({"agent_type": "reviewer"})   # a session started with --agent is the lead
    assert talk.hook(tmp_path, "code/001_x", sub) is None
    argv = ["hook", "--control", str(tmp_path), "--item", "code/001_x"]
    for raw in (json.dumps(sub), json.dumps({**sub, "agent_id": "x"})):
        monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(raw.encode())))
        assert talk.main(argv) == 0 and capsys.readouterr().out == ""
    assert len(talk.pending(tmp_path, "code/001_x")) == 1 and not talk.delivered(tmp_path)
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(b"not json")))
    assert talk.main(argv) == 0 and "MESSAGE #7" in capsys.readouterr().out
    assert [m["id"] for m in talk.delivered(tmp_path)] == [7]


def test_a_message_waits_out_a_subagent_call_for_the_lead(env):
    go, again = env.root / "go", env.root / "again"
    # Neither a helper's plain call nor its `progress` call (Word Warriors 059) takes the mail.
    env.queue("001_helper", f"FAKE wait-for {go.as_posix()} 30\nFAKE subagent Read\n"
                            'FAKE subagent progress {"note": "helper reading"}\n'
                            f"FAKE wait-for {again.as_posix()} 30\nFAKE write lead.txt mine")
    env.tick_until(lambda: (env.root / "go.waiting").exists())
    send(env, "by: app\ntell: 001 check the lead gets this")
    [tell] = env.events("tell")
    go.touch()
    env.tick_until(lambda: (env.root / "again.waiting").exists())
    env.d.tick()
    assert talk.pending(env.control, "code/001_helper") and not env.events("delivered")
    assert [e.detail["note"] for e in env.events("progress")] == ["helper reading"]
    again.touch()
    env.tick_until(lambda: env.finished("code/001_helper"))
    [got] = env.events("delivered")
    assert (got.detail["tell"], got.detail["via"]) == (tell.id, "hook")
    assert report(env, "001_helper").count(f"CONTEXT: MESSAGE #{tell.id} from Matt via app") == 1


def test_pre_hook_keeps_shell_calls_short(tmp_path):
    bg = ["npm test*", "*playwright test*"]
    def run(tool_input, tool="Bash"):
        return talk.pre_tool({"tool_name": tool, "tool_input": tool_input}, 60, bg)
    # The 034 waiter: one foreground poll loop of up to ten minutes.
    waiter = {"command": "timeout 590 bash -c 'until grep -q EXIT x.log; do sleep 10; done'", "timeout": 600000}
    assert run(waiter) == waiter | {"timeout": 60000}
    assert run({"command": "git status"}) == {"command": "git status", "timeout": 60000}   # default is 2 min
    assert run({"command": "git status", "timeout": 30000}) is None
    assert run({"command": "npm  test -- --grep slow", "timeout": 900000, "description": "d"}) == {
        "command": "npm  test -- --grep slow", "description": "d", "run_in_background": True}
    assert run({"command": "cd web && npx playwright test x", "timeout": 5000}, "PowerShell")["run_in_background"]
    assert run({"command": "npm test", "run_in_background": True}) is None
    assert run({"file_path": "x"}, "Read") is None
    assert talk.pre_tool({"tool_name": "Bash", "tool_input": {"command": "ls"}}, 0, []) is None
    out = json.loads(talk.pre_hook({"tool_name": "Bash", "tool_input": waiter}, 60, []))["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse" and out["updatedInput"]["timeout"] == 60000
    settings = json.loads(talk.hook_settings("py", tmp_path, "code/001_x", 60, bg))
    [group] = settings["hooks"]["PreToolUse"]
    assert group["matcher"] == "Bash|PowerShell"
    assert group["hooks"][0]["args"] == ["-m", "conductor.talk", "pre", "--cap", "60",
                                         "--background=npm test*", "--background=*playwright test*"]
    assert talk.main(["pre", "--cap", "60"]) == 0       # stdin is empty under pytest: prints nothing


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
