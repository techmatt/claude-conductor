"""Project tools: `[tools.<name>]`, the `tool:` signal and the Repos tab's buttons."""

import json
import socket
import sys

import psutil
import pytest

from conductor import config, procs, rename, targets
from conductor.app.model import Project

from conftest import git


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def add_tool(env, name: str, run, url: str, **extra) -> None:
    lines = [f"\n[tools.{name}]", f"run = {json.dumps(run)}", f"url = {json.dumps(url)}",
             *(f"{k} = {json.dumps(v)}" for k, v in extra.items())]
    toml = env.control / "conductor.toml"
    toml.write_text(toml.read_text(encoding="utf-8") + "\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    git(env.control, "commit", "-qam", f"tool {name}")
    env.d.log.close()
    env.d = env.new_daemon()


def send(env, text: str, name: str = "t.signal") -> None:
    env.signal(name, text)
    env.tick_until(lambda: not (env.control / "signals" / name).exists())


def panel_on(env):
    from conductor.app.repos_panel import ReposPanel
    project = Project(env.control)
    panel = ReposPanel(project, lambda _: None)
    panel.update_snapshot(project.snapshot())
    return project, panel


def settle_click(env, button, done) -> None:
    """A click writes an `app-*.signal`; the daemon takes it once it has settled."""
    button.click()
    env.tick_until(done)


@pytest.mark.slow
def test_button_and_signal_start_one_server_below_normal_and_the_daemon_stops_it(env, qapp):
    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    add_tool(env, "demo", f'"{sys.executable}" -m http.server {port} --bind 127.0.0.1', url, label="Demo server")
    opened = []
    env.d.tools.opener = opened.append
    project, panel = panel_on(env)
    b = panel.tool_buttons["demo"]
    assert panel.tool_bar.isHidden() is False and b.text() == "Demo server ● down"
    try:
        settle_click(env, b, lambda: env.events("run_open"))
        assert opened == [url] and len(env.events("run_start")) == 1
        start = env.events("run_start")[0]
        assert start.detail["tool"] is True and start.detail["by"] == "app"
        assert (env.control / ".conductor" / "tool-demo.log").is_file()
        # The shell and the server under it, both below normal.
        rec = targets.process(env.d.cfg, "demo", "tool")
        root = psutil.Process(rec["pid"])
        below = psutil.BELOW_NORMAL_PRIORITY_CLASS if procs.WINDOWS else 10
        assert [p.nice() for p in [root, *root.children(recursive=True)]] == [below] * (1 + len(root.children(recursive=True)))
        panel.update_snapshot(project.snapshot())
        assert b.text() == "Demo server ● running"

        # A second click only opens; so does the signal, for the planner.
        settle_click(env, b, lambda: len(env.events("run_open")) == 2)
        send(env, "by: planner\ntool: demo")
        assert opened == [url] * 3 and len(env.events("run_start")) == 1
        assert env.events("run_open")[-1].detail["by"] == "planner"

        # A daemon restart keeps it, and the next daemon still owns it.
        env.d.log.close()
        env.d = env.new_daemon()
        env.d.tools.opener = opened.append
        send(env, "tool: demo", "again")
        assert len(env.events("run_start")) == 1 and targets.process(env.d.cfg, "demo", "tool") == rec

        env.d._log_stop("signal")
        assert not procs.alive(rec["pid"], rec["create_time"])
        assert env.events("run_stop")[0].detail["note"] == "stopped with the daemon"
        assert env.events("run_stop")[0].detail["tool"] is True
        assert not (env.control / ".conductor" / "tool-demo.json").exists()
    finally:
        if rec := targets.process(env.d.cfg, "demo", "tool"):
            procs.kill_tree(rec["pid"])


@pytest.mark.slow
def test_a_failed_start_shows_its_log_tail_and_flags_no_watch(env, qapp):
    add_tool(env, "broken", f'"{sys.executable}" -c "print(\'boom: no viewer\'); raise SystemExit(3)"',
             f"http://127.0.0.1:{free_port()}/")
    project, panel = panel_on(env)
    project.poll_events()
    send(env, "tool: broken")
    env.tick_until(lambda: env.events("run_failed"))
    [failed] = env.events("run_failed")
    assert failed.detail["tool"] is True and "boom: no viewer" in failed.detail["log_tail"]
    assert "tool `broken` failed" in (env.control / "inbox.md").read_text(encoding="utf-8").split("## Red", 1)[1]
    panel.update_snapshot(project.snapshot(), project.poll_events())
    assert "boom: no viewer" in panel.tool_failure.text() and "boom: no viewer" in panel.tool_failure.toolTip()
    # The stall check flags a run target's failure, and not the tool's before it.
    env.d.log.append("run_failed", target="web", error="x")
    env.d.cfg.watch_check_minutes = 0.0001
    env.tick_until(lambda: env.events("watch_flag"))
    assert ["run target `web` failed: x"] == [e.detail["text"] for e in env.events("watch_flag")]


def test_a_project_with_no_tools_shows_no_bar(env, qapp):
    _, panel = panel_on(env)
    assert panel.tool_bar.isHidden() and not panel.tool_buttons


def test_tool_refusals(env):
    for n, text in enumerate(["tool: nope", "tool: a b", "tool:"]):
        send(env, text, f"t{n}")
        assert (env.control / "signals" / f"t{n}.bad").is_file(), text
    assert "no tool 'nope'" in env.events("invalid")[0].detail["error"]


def test_config_checks(env):
    toml = env.control / "conductor.toml"
    base = toml.read_text(encoding="utf-8")
    repo = next(iter(env.d.cfg.repos))

    def load(table: str):
        toml.write_text(base + "\n" + table, encoding="utf-8", newline="\n")
        return config.load(env.control)

    good = 'run = "x"\nurl = "http://localhost:1/"\n'
    t = load(f'[tools.a]\n{good}cwd = "{repo}/sub/dir"\nlabel = "Cards"\n').tools["a"]
    assert (t.label, t.cwd, t.ready) == ("Cards", env.d.cfg.repos[repo] / "sub" / "dir", "http://localhost:1/")
    t = load(f"[tools.a]\n{good}").tools["a"]        # one repo: `cwd` and `label` may go
    assert (t.label, t.cwd) == ("a", env.d.cfg.repos[repo])
    assert targets.ports(load(f"[tools.a]\n{good}")) == {1}
    for bad, why in ((f'[tools.a]\n{good}ready = "http://x/"\n', "unknown keys"),
                     ('[tools.a]\nurl = "http://x/"\n', "run must be"),
                     ('[tools.a]\nrun = "x"\n', "url must be"),
                     (f'[tools.a]\n{good}cwd = "nope"\n', "not in [repos]"),
                     (f'[tools.a]\n{good}cwd = "{repo}/../x"\n', "inside the repo"),
                     (f'[tools.a]\n{good}label = 3\n', "label must be"),
                     ("[tools]\na = 3\n", "must be tables")):
        with pytest.raises(config.ConfigError, match=why.replace("[", r"\[").replace("]", r"\]")):
            load(bad)


def test_a_rename_moves_a_tool_cwd_and_no_run_target_cwd():
    text = ('[run.web]\ncwd = "code/site"\n\n[tools.cards]\nrun = "x"\ncwd = "code/tools/viewer"\n\n'
            '[tools.other]\ncwd = "code"\n\n[tools.third]\ncwd = "codebase/x"\n')
    out = rename._rewrite_toml(text, "code", "game", None, None)
    assert out == ('[run.web]\ncwd = "code/site"\n\n[tools.cards]\nrun = "x"\ncwd = "game/tools/viewer"\n\n'
                   '[tools.other]\ncwd = "game"\n\n[tools.third]\ncwd = "codebase/x"\n')
