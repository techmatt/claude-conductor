"""Run targets: `[run.<name>]`, the `run:` signal and `conductor run-target`."""

import json
import socket
import sys

import pytest

from conductor import cli, config, procs, targets

from conftest import git


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def add_target(env, name: str, command, url: str, **extra) -> None:
    site = env.root / "site"
    site.mkdir(exist_ok=True)
    (site / "index.html").write_text("hello\n", encoding="utf-8")
    lines = [f"\n[run.{name}]", f"cwd = {json.dumps(str(site))}", f"command = {json.dumps(command)}",
             f"url = {json.dumps(url)}", *(f"{k} = {json.dumps(v)}" for k, v in extra.items())]
    toml = env.control / "conductor.toml"
    toml.write_text(toml.read_text(encoding="utf-8") + "\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    git(env.control, "commit", "-qam", f"target {name}")
    env.d.log.close()
    env.d = env.new_daemon()


def serve(port: int) -> list[str]:
    return [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"]


def send(env, text: str, name: str = "r.signal") -> None:
    env.signal(name, text)
    env.tick_until(lambda: not (env.control / "signals" / name).exists())


def test_run_starts_waits_opens_and_reuses(env):
    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    add_target(env, "web", serve(port), url)
    urls = []
    env.d.runner.opener = urls.append
    try:
        send(env, "by: planner\nrun: web")
        [start] = env.events("run_start")
        assert (start.detail["target"], start.detail["by"]) == ("web", "planner")
        env.tick_until(lambda: env.events("run_open"))
        assert env.events("run_ready")[0].detail["by"] == "planner"
        assert urls == [url] and targets.status(env.d.cfg, env.d.cfg.targets["web"]) == "up"
        # Up already: just opened again, nothing started.
        send(env, "run: web", "again")
        assert len(env.events("run_start")) == 1 and len(env.events("run_open")) == 2 and urls == [url, url]
        pid = targets.process(env.d.cfg, "web")["pid"]
        send(env, "by: app\nrun: web stop", "stop")
        assert not procs.alive(pid, None) and env.events("run_stop")[0].detail["by"] == "app"
        assert targets.status(env.d.cfg, env.d.cfg.targets["web"]) == "down"
    finally:
        if rec := targets.process(env.d.cfg, "web"):
            procs.kill_tree(rec["pid"])


def test_a_target_that_never_comes_up_is_red_with_its_log(env):
    port = free_port()
    add_target(env, "broken", f'"{sys.executable}" -c "print(\'boom: no config\'); raise SystemExit(3)"',
               f"http://127.0.0.1:{port}/", ready_seconds=20)
    send(env, "run: broken")
    env.tick_until(lambda: env.events("run_failed"))
    [failed] = env.events("run_failed")
    assert "exited before" in failed.detail["error"] and "boom: no config" in failed.detail["log_tail"]
    assert not env.events("run_open")
    assert "run target `broken` failed" in (env.control / "inbox.md").read_text(encoding="utf-8").split("## Red", 1)[1]
    assert not env.d.log.query(kinds=["red"])          # a red event of its own kind: no queue is held


def test_ready_timeout_leaves_the_process_running(env):
    add_target(env, "slow", serve(free_port()), f"http://127.0.0.1:{free_port()}/", ready_seconds=1.5)
    try:
        send(env, "run: slow")
        env.tick_until(lambda: env.events("run_failed"))
        assert "did not answer within" in env.events("run_failed")[0].detail["error"]
        assert targets.process(env.d.cfg, "slow") is not None
    finally:
        if rec := targets.process(env.d.cfg, "slow"):
            procs.kill_tree(rec["pid"])


def test_run_refusals(env):
    for n, text in enumerate(["run: nope", "run: a b", "run:"]):
        send(env, text, f"r{n}")
        assert (env.control / "signals" / f"r{n}.bad").is_file(), text
    assert "no target 'nope'" in env.events("invalid")[0].detail["error"]


def test_config_checks(env):
    def load(table: str):
        toml = env.control / "conductor.toml"
        base = toml.read_text(encoding="utf-8").split("\n[run.", 1)[0]
        toml.write_text(base + table, encoding="utf-8", newline="\n")
        return config.load(env.control)

    t = load('\n[run.game]\nrepo = "code"\ncommand = "npm run dev"\nurl = "http://localhost:5173/"\n')
    g = t.targets["game"]
    assert (g.cwd, g.command, g.ready, g.ready_seconds) == (env.repo.resolve(), "npm run dev", "http://localhost:5173/", 60)
    for bad in ('\n[run.g]\ncommand = "x"\nurl = "http://h/"\n',                              # no repo or cwd
                '\n[run.g]\nrepo = "nope"\ncommand = "x"\nurl = "http://h/"\n',               # unknown repo
                '\n[run.g]\nrepo = "code"\ncommand = "x"\nurl = "h"\n',                       # not a URL
                '\n[run.g]\nrepo = "code"\nurl = "http://h/"\n',                              # no command
                '\n[run.g]\nrepo = "code"\ncommand = "x"\nurl = "http://h/"\nport = 1\n'):    # unknown key
        with pytest.raises(config.ConfigError):
            load(bad)


def test_cli_run_target(env, monkeypatch, capsys):
    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    add_target(env, "web", serve(port), url)
    urls = []
    monkeypatch.setattr(targets.webbrowser, "open", urls.append)
    try:
        assert cli.main(["run-target", "web", "--control", str(env.control)]) == 0
        out = capsys.readouterr().out
        assert "started" in out and "ready after" in out and f"opened {url}" in out and urls == [url]
        assert env.d.log.last("run_open").detail["by"] == "cli"
        assert cli.main(["run-target", "web", "--stop", "--control", str(env.control)]) == 0
        assert targets.process(env.d.cfg, "web") is None
        assert cli.main(["run-target", "nope", "--control", str(env.control)]) == 1
    finally:
        if rec := targets.process(env.d.cfg, "web"):
            procs.kill_tree(rec["pid"])
