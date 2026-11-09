"""The two hooks, and containment: without both opt-ins nothing changes (no broker, no
port, no build; preamble, environment, inbox and digest the same text)."""

import json
import os
import re
import shutil
import subprocess
import time

import psutil
import pytest

from conductor import procs, remote
from conductor.remote import settings

from .conftest import remote_section

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
TIME = re.compile(r"\d{4}-\d\d-\d\dT[\d:.]+(?:[+-]\d\d:?\d\d|Z)?|\b\d{1,2}:\d\d(?::\d\d)?\s?(?:am|pm)\b", re.I)


@pytest.fixture
def launches(monkeypatch):
    """Each executor launch: its extra environment and the prompt it was sent."""
    seen = []
    real = procs.spawn

    def spawn(cmd, cwd, stdin, stdout, stderr, env=None):
        seen.append({"env": dict(env or {}), "prompt": stdin.read_text(encoding="utf-8")})
        return real(cmd, cwd, stdin, stdout, stderr, env=env)
    monkeypatch.setattr(procs, "spawn", spawn)
    return seen


@pytest.fixture
def no_broker(monkeypatch):
    started = []
    monkeypatch.setattr(procs, "spawn_detached", lambda *a, **k: started.append(a) or procs.Detached(0, None))
    return started


def norm(text: str, p) -> str:
    for root in (str(p.root), p.root.as_posix(), str(p.root).replace("\\", "\\\\")):
        text = text.replace(root, "<ROOT>")
    text = re.sub(r"^- memory .*$", "- memory <M>", text, flags=re.M)      # the box's live reading
    return re.sub(r"\b[0-9a-f]{7,40}\b", "<SHA>", TIME.sub("<T>", UUID.sub("<ID>", text)))


def run_one(p, launches, **meta) -> dict:
    p.queue("001_job", "FAKE note hi", **meta)
    p.tick_until(lambda: p.d.log.last("finish", item="code/001_job"))
    p.d.write_inbox(force=True)
    p.d._session("start", "signals/s")
    port = str(p.d.log.last("start", item="code/001_job").detail["port"])
    got = launches[-1]
    env = {k: (v if k != "CONDUCTOR_PORT" else "<PORT>") for k, v in got["env"].items()}
    return {"env": env, "prompt": norm(got["prompt"].replace(port, "<PORT>"), p),
            "inbox": norm((p.control / "inbox.md").read_text(encoding="utf-8"), p),
            "digest": norm((p.control / "session.md").read_text(encoding="utf-8"), p)}


@pytest.mark.slow
def test_without_both_opt_ins_nothing_changes(project, launches, no_broker):
    plain, flagged = project("plain"), project("flagged")
    base = run_one(plain, launches)
    assert base["env"] == {"CONDUCTOR_PORT": "<PORT>"}
    assert run_one(flagged, launches, remote="true") == base             # the item opts in, the project not
    assert not no_broker
    configured = project("configured", remote_section())                # the project opts in, the item not
    assert len(no_broker) == 1                                           # (its broker, stood in)
    assert run_one(configured, launches) == base
    for p in (plain, flagged):                                           # no broker, build or state at all
        assert not (p.control / ".conductor" / "remote").exists()
        assert not (p.control / ".conductor" / "now").exists()
    assert not (configured.control / ".conductor" / "remote" / "bundles").exists()   # no build either


def test_no_section_means_no_broker_no_port_no_files(project, no_broker, tmp_path):
    p = project("plain")
    assert not no_broker
    assert not (p.control / ".conductor" / "remote").exists()
    assert not (p.control / ".conductor" / "now").exists()
    prompt = tmp_path / "prompt.md"
    prompt.write_text("body\n", encoding="utf-8")
    assert remote.item_launch(p.control, prompt) == {}
    assert prompt.read_text(encoding="utf-8") == "body\n"
    assert not (p.control / ".conductor" / "remote").exists()


@pytest.mark.slow
def test_a_remote_item_gets_farm_seeds_on_its_path(project, launches, no_broker):
    p = project("on", remote_section())
    p.queue("001_job", "FAKE note hi", remote="true")
    p.tick_until(lambda: p.d.log.last("finish", item="code/001_job"))
    env, prompt = launches[-1]["env"], launches[-1]["prompt"]
    bin_dir = p.control / ".conductor" / "remote" / "bin"
    path_key = next(k for k in env if k.upper() == "PATH")
    assert env[path_key].split(os.pathsep)[0] == str(bin_dir)
    assert env[settings.CONTROL_VAR] == str(p.control)
    assert env[settings.ITEM_VAR] == "code/001_job"           # the worker page names the item
    assert prompt.rstrip().endswith("Its last stderr line says where\nthe shards ran.")
    assert "`farm-seeds <first seed> <count> <out file> [args…]`" in prompt
    # The command on that PATH works: no broker answers, so it runs locally.
    exe = shutil.which(settings.COMMAND, path=env[path_key])
    out = p.root / "seeds.txt"
    done = subprocess.run([exe, "0", "30", str(out)], cwd=p.repo, env={**os.environ, **env}, capture_output=True,
                          text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert out.read_text(encoding="utf-8").count("\n") == 30
    assert "running locally" in done.stderr


@pytest.mark.slow
def test_the_daemon_starts_one_broker_and_keeps_it_across_a_restart(project):
    p = project("live", remote_section())
    info_path = p.control / ".conductor" / "remote" / "broker.json"
    try:
        end = time.time() + 30
        while not info_path.exists() and time.time() < end:
            time.sleep(0.1)
        info = json.loads(info_path.read_text(encoding="utf-8"))
        assert psutil.pid_exists(info["pid"])
        from conductor.remote.client import Broker
        assert Broker(info["local"]).call("status", {})["live"] == 0
        remote.start_broker(p.control, lambda n: pytest.fail(n))          # same settings: kept
        assert json.loads(info_path.read_text(encoding="utf-8"))["pid"] == info["pid"]
        toml = p.control / "conductor.toml"
        toml.write_text(toml.read_text(encoding="utf-8").replace("shard_seeds = 10", "shard_seeds = 11"),
                        encoding="utf-8")
        remote.start_broker(p.control, lambda n: pytest.fail(n))          # other settings: replaced
        end = time.time() + 30
        while time.time() < end:
            try:
                if json.loads(info_path.read_text(encoding="utf-8"))["pid"] != info["pid"]:
                    break
            except (OSError, ValueError):
                pass
            time.sleep(0.1)
        new = json.loads(info_path.read_text(encoding="utf-8"))["pid"]
        assert new != info["pid"] and not psutil.pid_exists(info["pid"])
    finally:
        for proc in psutil.process_iter(["cmdline"]):
            if str(p.control) in " ".join(proc.info["cmdline"] or []) and proc.pid != os.getpid():
                try:
                    proc.kill()
                except psutil.Error:
                    pass


def test_a_bad_section_warns_and_starts_nothing(project, no_broker):
    p = project("bad", "\n[remote]\nnative = [\"x\"]\nfingerprint = [\"y\"]\n")
    assert not no_broker
    warn = p.d.log.last("warning")
    assert warn and "remote workers off" in warn.detail["note"] and "{out}" in warn.detail["note"]
    q = project("quote", "\n[remote]\nnative = \"sim 'x\"\nfingerprint = [\"y\"]\n")   # shlex can't split it
    assert "No closing quotation" in q.d.log.last("warning").detail["note"]
    assert remote.item_launch(q.control, q.root / "nothing.md") == {}


def test_a_hook_that_breaks_never_stops_the_daemon(project, monkeypatch):
    from conductor.remote import settings as s

    def boom(control):
        raise RuntimeError("boom")
    monkeypatch.setattr(s, "load", boom)
    p = project("boom")
    assert "boom" in p.d.log.last("warning").detail["note"]
    assert remote.item_launch(p.control, p.root / "x.md") == {}


def test_the_now_line_shows_only_while_fresh(project):
    from conductor import inbox
    p = project("now")
    line = p.control / ".conductor" / "now" / "remote.line"
    line.parent.mkdir(parents=True)
    line.write_text("remote workers: 1 connected (1 browser), 7 cores\n", encoding="utf-8")
    assert inbox.now_files(p.d.cfg) == ["remote workers: 1 connected (1 browser), 7 cores"]
    old = time.time() - 60
    os.utime(line, (old, old))
    assert inbox.now_files(p.d.cfg) == []
