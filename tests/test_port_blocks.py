"""Port blocks (portblocks.py): each control folder owns a block of ports, one registry on the
machine; and a run target or tool counts as up only when its own process holds its port."""

import json
import os
import random
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conductor import config, portblocks, procs, request, targets


@pytest.fixture
def registry(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "ports.json"
    monkeypatch.setenv("CONDUCTOR_PORTS", str(path))
    return path


def control(root: Path, project: str, body: str = "") -> Path:
    c = root / f"{project}-control"
    (c / "repo").mkdir(parents=True, exist_ok=True)
    (c / "conductor.toml").write_text(f'project = "{project}"\n{body}\n[repos]\nmain = "repo"\n',
                                      encoding="utf-8", newline="\n")
    return c


def tool(name: str, port: int) -> str:
    return f'\n[tools.{name}]\nrun = "npm run {name}"\nurl = "http://localhost:{port}/"\n'


def test_parse_takes_only_whole_aligned_blocks():
    assert portblocks.parse("5260-5279") == range(5260, 5280)
    for bad in ("5261-5280", "5260-5270", "100-119", 5260, "5260"):
        with pytest.raises(portblocks.BlockError):
            portblocks.parse(bad)


def test_ports_in_the_block_load_and_claim_it(tmp_path, registry):
    c = control(tmp_path, "alpha", 'ports = "5220-5239"' + tool("cards", 5220)
                + '\n[remote]\nport = 5239\n')
    cfg = config.load(c)
    assert cfg.ports == range(5220, 5240)
    assert json.loads(registry.read_text(encoding="utf-8")) == {
        "5220": {"control": c.resolve().as_posix(), "project": "alpha"}}


@pytest.mark.parametrize("body, said", [
    ('ports = "5220-5239"' + tool("cards", 5180), "tools.cards names port 5180, outside this project's block 5220-5239"),
    ('ports = "5220-5239"\n[run.web]\nrepo = "main"\ncommand = "npm run dev -- --port 5300"\nurl = "http://localhost:5221/"\n',
     "run.web command names port 5300"),
    ('ports = "5220-5239"\n[remote]\ntoken = "x"\n', "[remote] port (its default) names port 8770"),
    (tool("cards", 5180), 'add `ports = "5200-5219"` (the next free block)'),
])
def test_a_port_outside_the_block_is_refused(tmp_path, registry, body, said):
    with pytest.raises(config.ConfigError, match=None) as exc:
        config.load(control(tmp_path, "alpha", body))
    assert said in str(exc.value)


def test_another_projects_block_or_port_is_refused_naming_it(tmp_path, registry):
    a = control(tmp_path, "aicomplete", 'ports = "5200-5219"' + tool("cards", 5200))
    config.load(a)
    with pytest.raises(config.ConfigError) as exc:
        config.load(control(tmp_path, "helium", 'ports = "5220-5239"' + tool("game", 5200)))
    assert "names port 5200, in aicomplete's block 5200-5219" in str(exc.value)
    with pytest.raises(config.ConfigError) as exc:
        config.load(control(tmp_path, "helium", 'ports = "5200-5219"'))
    assert "is aicomplete's block" in str(exc.value) and "next free block is 5220-5239" in str(exc.value)
    # A block its folder no longer declares is free again: no clean-up step.
    (a / "conductor.toml").write_text('project = "aicomplete"\n[repos]\nmain = "repo"\n', encoding="utf-8")
    assert config.load(control(tmp_path, "helium", 'ports = "5200-5219"')).ports.start == 5200
    assert list(json.loads(registry.read_text(encoding="utf-8"))) == ["5200"]


def test_a_conductor_toml_request_into_another_projects_block_is_refused(tmp_path, registry):
    config.load(control(tmp_path, "aicomplete", 'ports = "5200-5219"'))
    c = control(tmp_path, "helium", 'ports = "5220-5239"')
    config.load(c)
    d = c / "requests" / "20261114-2200-game"
    d.mkdir(parents=True)
    (d / "request.md").write_text(
        "---\ntargets: [conductor.toml]\nmessage: add the game\n---\n<<<<<<< conductor.toml\n"
        'ports = "5220-5239"\n=======\nports = "5220-5239"\n\n[run.game]\nrepo = "main"\n'
        'command = "npm run dev"\nurl = "http://localhost:5205/"\n>>>>>>>\n', encoding="utf-8", newline="\n")
    old = time.time() - 10
    os.utime(d / "request.md", (old, old))
    with pytest.raises(request.RequestError) as exc:
        request.ready(d, c)
    assert "would not load" in str(exc.value) and "aicomplete's block 5200-5219" in str(exc.value)
    assert "[run.game]" not in (c / "conductor.toml").read_text(encoding="utf-8")


def test_off_checks_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("CONDUCTOR_PORTS", "off")
    assert config.load(control(tmp_path, "alpha", tool("cards", 5180))).ports is None


# ---------------------------------------------------------------- who holds a port

class Log:
    def __init__(self):
        self.events = []

    def append(self, kind, **detail):
        self.events.append((kind, detail))

    def kinds(self):
        return [k for k, _ in self.events]


def free_block() -> range:
    """An aligned block whose first two ports nothing on this box listens on; a random one, so
    parallel workers rarely meet (a met one fails a bind, and is passed over)."""
    blocks = list(range(portblocks.BASE + 40 * portblocks.SIZE, portblocks.TOP - portblocks.SIZE, portblocks.SIZE))
    random.shuffle(blocks)
    for start in blocks:
        try:
            for port in (start, start + 1):
                with socket.socket() as s:
                    s.bind(("127.0.0.1", port))
            return range(start, start + portblocks.SIZE)
        except OSError:
            continue
    raise RuntimeError("no free block")


def listen(port: int) -> socket.socket:
    s = socket.socket()
    s.bind(("127.0.0.1", port))
    s.listen()
    return s


def test_a_stranger_on_the_port_fails_the_run_and_opens_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("CONDUCTOR_PORTS", "off")
    port = free_block().start
    c = control(tmp_path, "helium", f'[run.game]\nrepo = "main"\ncommand = "unused"\nurl = "http://localhost:{port}/"\n')
    cfg, log, opened = config.load(c), Log(), []
    with listen(port):
        assert targets.status(cfg, cfg.targets["game"]) == "taken"
        targets.Runner(cfg, log, opened.append).request("game")
    [(kind, detail)] = log.events
    assert kind == "run_failed" and not opened
    assert f"port {port} is held by {procs.name(os.getpid())} (pid {os.getpid()}), which conductor did not start" \
        in detail["error"]


def test_the_holder_is_named_by_its_project_and_tool(tmp_path, registry):
    block = free_block()
    a = control(tmp_path, "aicomplete", f'ports = "{portblocks.text(block)}"' + tool("cards", block.start))
    config.load(a)
    (a / ".conductor").mkdir()
    (a / ".conductor" / "tool-cards.json").write_text(json.dumps(
        {"pid": os.getpid(), "create_time": procs.create_time(os.getpid())}), encoding="utf-8")
    h = control(tmp_path, "helium", f'ports = "{portblocks.text(range(block.start + 20, block.stop + 20))}"')
    cfg = config.load(h)
    assert targets.who(cfg, os.getpid()) == f"aicomplete's tool cards (pid {os.getpid()})"


def test_a_process_started_under_an_older_command_is_replaced(tmp_path, monkeypatch):
    """A moved port: the old server, on the old port, is stopped and the new command started,
    rather than waited on as `starting` until ready_seconds pass."""
    monkeypatch.setenv("CONDUCTOR_PORTS", "off")
    port = free_block().start
    serve = [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"]
    c = control(tmp_path, "helium", f'[run.game]\nrepo = "main"\ncommand = {json.dumps(serve)}\n'
                f'url = "http://127.0.0.1:{port}/"\n')
    cfg, log = config.load(c), Log()
    old = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        cfg.state_dir.mkdir()
        targets.state_path(cfg, "game").write_text(json.dumps(
            {"pid": old.pid, "create_time": procs.create_time(old.pid), "command": "npm run dev -- --port 5180"}),
            encoding="utf-8")
        targets.Runner(cfg, log, lambda url: None).request("game")
        assert log.kinds() == ["run_stop", "run_start"] and "command changed" in log.events[0][1]["note"]
        assert old.wait(10) is not None
    finally:
        old.kill()
        if rec := targets.process(cfg, "game"):
            procs.kill_tree(rec["pid"])


@pytest.mark.slow
def test_the_game_starts_beside_another_projects_card_viewer(tmp_path, registry):
    """Prompt 119's case: aicomplete's card viewer up, Helium Hustle's Run game starts its own."""
    block = free_block()
    a = control(tmp_path, "aicomplete", f'ports = "{portblocks.text(block)}"' + tool("cards", block.start))
    config.load(a)
    mine = range(block.start + 20, block.stop + 20)
    serve = json.dumps([sys.executable, "-m", "http.server", str(mine.start), "--bind", "127.0.0.1"])
    h = control(tmp_path, "helium", f'ports = "{portblocks.text(mine)}"\n[run.game]\nrepo = "main"\n'
                f'command = {serve}\nurl = "http://127.0.0.1:{mine.start}/"\nready_seconds = 30\n')
    cfg, log, opened = config.load(h), Log(), []
    runner = targets.Runner(cfg, log, opened.append)
    with listen(block.start):           # the card viewer
        try:
            runner.request("game")
            deadline = time.monotonic() + 30
            while runner.pending and time.monotonic() < deadline:
                runner.poll()
                time.sleep(0.2)
            assert log.kinds() == ["run_start", "run_ready", "run_open"]
            assert opened == [f"http://127.0.0.1:{mine.start}/"]
            assert targets.status(cfg, cfg.targets["game"]) == "up"
        finally:
            if rec := targets.process(cfg, "game"):
                procs.kill_tree(rec["pid"])
