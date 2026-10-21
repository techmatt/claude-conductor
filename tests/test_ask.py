import json
import sys
import time

import pytest
from datetime import timedelta

from conductor import cli, config, procs, questions, scaffold, talk
from conductor.daemon import Daemon
from conductor.util import now, stamp

from pathlib import Path

from conftest import FAKE, git
from mcp_client import Client


def ask_line(**args) -> str:
    return "FAKE ask " + json.dumps(args)


def report(env, name: str) -> str:
    return (env.control / "reports" / f"code__{name}.md").read_text(encoding="utf-8")


@pytest.mark.slow
def test_blocking_question_answered_by_file(env):
    env.queue("001_ask", ask_line(question="Which store?", options=["sqlite", "postgres"], block=True))
    env.tick_until(lambda: env.events("ask"))
    [q] = questions.scan(env.control)
    assert q.item == "code/001_ask" and q.block and q.deadline is None and "- postgres" in q.body
    assert q.path.name == "code__001_ask__1.md"
    inbox = (env.control / "inbox.md").read_text(encoding="utf-8")
    assert inbox.index("## Questions") < inbox.index("## Now") and q.id in inbox
    assert env.toasts[0][0] == "test: question from code/001_ask"
    # Past its deadline while blocked: no kill. The deadline is pulled in to 3s from now
    # rather than waited out; the answer then moves it later by the time spent blocked.
    run = env.d.runs["code/001_ask"]
    run.rec["deadline"] = stamp(now() + timedelta(seconds=3))
    moved = run.rec["deadline"]
    time.sleep(3.5)
    for _ in range(3):
        env.d.tick()
    assert not env.events("kill")
    q.answer_path.write_text("Use sqlite.\n", encoding="utf-8")      # as the planner would
    env.tick_until(lambda: env.finished("code/001_ask"))
    assert env.finished("code/001_ask").detail["status"] == "done"
    [ans] = env.events("answer")
    assert ans.detail["text"] == "Use sqlite." and ans.detail["waited_seconds"] >= 4
    assert ans.detail["deadline"] > moved
    assert "Answer to code__001_ask__1:\nUse sqlite." in report(env, "001_ask")
    env.d.write_inbox(force=True)
    assert "## Questions\n" not in (env.control / "inbox.md").read_text(encoding="utf-8")


@pytest.mark.slow
def test_default_taken_then_late_answer(env):
    env.queue("001_def", ask_line(question="Name the table?", default="items"))
    env.tick_until(lambda: env.finished("code/001_def"))
    [d] = env.events("default")
    assert d.detail["text"] == "items" and d.detail["outcome"] == "default"
    assert "default was taken: items" in report(env, "001_def")
    [q] = questions.scan(env.control)
    assert not q.block and q.deadline
    q.answer_path.write_text("things\n", encoding="utf-8")
    time.sleep(1.1)
    env.tick_until(lambda: env.events("late"))
    assert env.events("late")[0].detail["text"] == "things"
    for _ in range(3):
        env.d.tick()
    assert not env.events("answer") and len(env.events("late")) == 1


def test_question_closed_when_item_dies(env):
    env.queue("001_dies", ask_line(question="Wait forever?", block=True))
    env.tick_until(lambda: env.events("ask"))
    procs.kill_tree(env.events("start")[0].detail["pid"])
    env.tick_until(lambda: env.finished("code/001_dies"))
    [c] = env.events("question_closed")
    assert c.detail["outcome"] == "closed"
    assert questions.scan(env.control)[0].result["by"] == "daemon"


@pytest.mark.slow
def test_toasts_for_red_hold_and_empty_queue(env):
    env.queue("001_bad", "FAKE report red")
    env.queue("002_next", "FAKE report done")
    env.tick_until(lambda: env.events("hold"))
    titles = [t for t, _, _ in env.toasts]
    assert "test: code/001_bad is red" in titles and "test: code/002_next held" in titles
    env.signal("clear.txt", "clear: code")
    env.tick_until(lambda: env.events("queue_empty"))
    assert env.toasts[-1][0] == "test: queue `code` is empty" and "code/002_next" in env.toasts[-1][1]
    assert all(tag.startswith("test#") for _, _, tag in env.toasts)


def test_failing_toast_is_logged_not_fatal(env):
    def broken(title, body, tag):
        raise OSError("no notification service")
    env.d.notifier = broken
    env.queue("001_bad", "FAKE report red")
    env.tick_until(lambda: env.finished("code/001_bad"))
    assert any("toast" in w.detail["note"] for w in env.events("warning"))


def test_ask_server_protocol(env, tmp_path):
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"conductor": {"command": sys.executable, "args": [
        "-m", "conductor.ask_server", "--control", str(env.control), "--item", "code/009_x"]}}}),
        encoding="utf-8")
    c = Client(cfg)
    try:
        tool, prog = c.start()
        assert tool["name"] == "ask" and tool["inputSchema"]["required"] == ["question"]
        # `note` may be left out with an `outline`, so the server checks it, not the schema.
        assert prog["name"] == "progress" and "required" not in prog["inputSchema"]
        assert prog["inputSchema"]["properties"]["outline"]["items"]["required"] == ["title", "eta"]
        bad = c.request("tools/call", {"name": "ask", "arguments": {"question": ""}})
        assert bad["isError"]
        assert c.request("ping") == {}
        # progress returns at once, and leaves a waiting message to the hook (lead-only).
        assert c.request("tools/call", {"name": "progress", "arguments": {"note": "x", "done": "two"}})["isError"]
        talk.post(env.control, "code/009_x", 41, "use sqlite", "app")
        got = c.request("tools/call", {"name": "progress", "arguments": {"note": "  reading   the code ",
                                                                         "eta": "25m", "done": 1, "remaining": 3}})
        text = got["content"][0]["text"]
        assert not got["isError"] and text == "Logged; carry on." and "MESSAGE #41" not in text
    finally:
        c.close()
    assert not questions.scan(env.control)
    [line] = talk.read_progress(env.control, "code/009_x")
    assert (line["seq"], line["note"], line["eta"], line["done"], line["remaining"]) == (1, "reading the code", "25m", 1, 3)
    assert not talk.delivered(env.control) and len(talk.pending(env.control, "code/009_x")) == 1


def test_ask_server_sends_progress_while_waiting(env, monkeypatch):
    import io
    import threading
    from conductor import ask_server
    monkeypatch.setattr(ask_server, "PROGRESS_SECONDS", 0.2)
    out = io.StringIO()
    server = ask_server.Server(env.control, "code/009_p", 10, None, out=out)
    t = threading.Thread(target=server.handle, args=({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {
        "name": "ask", "arguments": {"question": "Go?", "block": True}, "_meta": {"progressToken": "tok"}}},))
    t.start()
    time.sleep(1.0)
    [q] = questions.scan(env.control)
    questions.write_answer(q, "go")
    end = time.time() + 10
    while '"id": 7' not in out.getvalue() and time.time() < end:
        time.sleep(0.1)
    msgs = [json.loads(ln) for ln in out.getvalue().splitlines()]
    progress = [m for m in msgs if m.get("method") == "notifications/progress"]
    assert len(progress) >= 2 and all(m["params"]["progressToken"] == "tok" for m in progress)
    assert msgs[-1]["result"]["content"][0]["text"].endswith("go")


@pytest.mark.slow
def test_cli_questions_and_answer(env, capsys):
    env.queue("001_cli", ask_line(question="Proceed?", options=["yes", "no"], block=True))
    env.tick_until(lambda: env.events("ask"))
    assert cli.main(["questions", str(env.control)]) == 0
    assert "code__001_cli__1" in capsys.readouterr().out
    assert cli.main(["answer", "001__1", "yes,", "go", "--control", str(env.control)]) == 0
    env.tick_until(lambda: env.finished("code/001_cli"))
    assert env.events("answer")[0].detail["text"] == "yes, go"
    assert cli.main(["answer", "001__1", "again", "--control", str(env.control)]) == 1


def test_init_creates_folder_that_runs_an_item(env, tmp_path, capsys):
    control = tmp_path / "fresh-control"
    assert cli.main(["init", str(control), "--project", "fresh", "--repo", f"code={env.repo}"]) == 0
    cfg = config.load(control)
    assert cfg.project == "fresh" and cfg.repos["code"] == env.repo.resolve()
    assert '"../code"' in (control / "conductor.toml").read_text(encoding="utf-8")
    assert "push = false" in (control / "conductor.toml").read_text(encoding="utf-8") and not cfg.opts("code").push
    assert git(control, "ls-files").split() == [".gitattributes", ".gitignore", "conductor.toml", "state.md"]
    assert len(git(control, "log", "--format=%H").split()) == 1
    assert (control / "queue" / "code").is_dir()
    assert cli.main(["init", str(control), "--project", "again", "--repo", f"code={env.repo}"]) == 1
    assert "never overwrites" in capsys.readouterr().err
    # Point it at the fake executor (a drift, accepted at start) and run one item.
    toml = control / "conductor.toml"
    command = json.dumps([Path(sys.executable).as_posix(), FAKE.as_posix()])
    toml.write_text(toml.read_text(encoding="utf-8") + f"\n[executor]\ncommand = {command}\n",
                    encoding="utf-8", newline="\n")
    d = Daemon(config.load(control), echo=None, notifier=env.notifier)
    d.startup()
    (control / "queue" / "code" / "001_first.md").write_text("---\nrepo: code\n---\nFAKE report done\n",
                                                               encoding="utf-8")
    end = time.time() + 30
    while not d.log.last("finish", item="code/001_first") and time.time() < end:
        d.tick()
        time.sleep(0.05)
    try:
        assert d.log.last("finish", item="code/001_first").detail["status"] == "done"
        assert not [e for e in d.log.query(kinds=["warning"]) if "not git-ignored" in e.detail["note"]]
    finally:
        d.log.close()


def test_gitignore_covers_runtime():
    text = scaffold.gitignore()
    for name in config.RUNTIME:
        assert any(ln.rstrip("/*") == name for ln in text.splitlines()), name
