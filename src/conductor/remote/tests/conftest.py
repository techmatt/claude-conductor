"""Fixtures for the remote-workers tests. They live inside the module, so deleting it takes
them too; the few root-conftest fixtures they need are repeated here."""

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from conductor import config, daemon, daemon_signals, request
from conductor.remote import broker, client, settings, worker
from conductor.remote.board import Board

ROOT = Path(__file__).resolve().parents[4]
FAKE_CLAUDE = ROOT / "tests" / "fake_claude.py"
FAKE_SIM = Path(__file__).with_name("fake_sim.py")
WAIT = 120.0
GITIGNORE = "queue/\nproposed/\nrunning/\ndone/\nreports/\nquestions/\npicks/\nrequests/\nsignals/\ninbox.md\n" \
            "session.md\nreview.md\nevents.db*\n.conductor/\n"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


def init_repo(path: Path, files: dict[str, str]) -> None:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main", "--template=")
    git(path, "config", "user.name", "test")
    git(path, "config", "user.email", "test@example.com")
    for name, text in files.items():
        (path / name).write_text(text, encoding="utf-8", newline="\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "init")


def remote_section(**over) -> str:
    """A `[remote]` table driving `fake_sim.py`; `over` replaces or adds keys (TOML values)."""
    py, sim = Path(sys.executable).as_posix(), FAKE_SIM.as_posix()
    keys = {"native": f'["{py}", "{sim}", "{{first}}", "{{count}}", "{{out}}", "{{args}}"]',
            "fingerprint": f'["{py}", "{sim}", "fingerprint"]', "port": "0", "bind": '"127.0.0.1"',
            "shard_seeds": "10", "seed_seconds": "1", "local_below_seconds": "5", "lease_seconds": "30",
            "fresh_seconds": "5", "token": '"tok-test"', **over}
    keys = {k: v for k, v in keys.items() if v is not None}          # None: leave the key out
    return "\n[remote]\n" + "".join(f"{k} = {v}\n" for k, v in keys.items())


class Project:
    """A code repo and control folder with the fake executor, and a daemon over them."""

    def __init__(self, root: Path, remote: str | None = None):
        self.root, self.control, self.repo = root, root / "control", root / "code"
        init_repo(self.repo, {"README.md": "code\n"})
        toml = ('project = "test"\n\n[defaults]\nmax_hours = 1\npoll_seconds = 0.1\nask_default_minutes = 0.03\n'
                'memory_pressure_percent = 0\nmemory_pressure_free_gb = 0\nmemory_critical_percent = 0\n'
                'memory_critical_free_gb = 0\nwatch_check_minutes = 0\n\n'
                f'[repos]\ncode = "../code"\n\n[executor]\ncommand = ["{Path(sys.executable).as_posix()}", '
                f'"{FAKE_CLAUDE.as_posix()}"]\n') + (remote or "")
        init_repo(self.control, {"conductor.toml": toml, "state.md": "# state\n", ".gitignore": GITIGNORE,
                                 ".gitattributes": "* text=auto eol=lf\n"})
        self.lines: list[str] = []
        self.d = daemon.Daemon(config.load(self.control), echo=self.lines.append, notifier=lambda *a: None)
        self.d._load.sample = lambda owned: None
        self.d.startup()

    def queue(self, name: str, body: str, **meta) -> Path:
        fm = "\n".join(f"{k}: {v}" for k, v in {"repo": "code", **meta}.items())
        p = self.control / "queue" / "code" / f"{name}.md"
        p.write_text(f"---\n{fm}\n---\n{body}\n", encoding="utf-8")
        return p

    def tick_until(self, cond, timeout: float = WAIT) -> None:
        end = time.time() + timeout
        while time.time() < end:
            self.d.tick()
            if cond():
                return
            time.sleep(0.05)
        raise AssertionError("timed out; events:\n" + "\n".join(self.lines))

    def close(self) -> None:
        from conductor import procs
        for run in list(self.d.runs.values()):
            if run.pid():
                procs.kill_tree(run.pid())
        self.d.log.close()


@pytest.fixture(autouse=True)
def quiet_daemon(tmp_path, monkeypatch):
    from conductor import daemon_restart, procs, session
    monkeypatch.setattr(procs, "claude_ancestor", lambda: None)
    monkeypatch.setattr(daemon_restart, "code_version", lambda root=None: None)
    monkeypatch.setattr(session, "CONDUCTOR_ROOT", tmp_path / "no-conductor")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setattr(request, "FRESH_SECONDS", 0)
    monkeypatch.setattr(daemon_signals, "SIGNAL_SETTLE_SECONDS", -0.01)
    monkeypatch.setattr(daemon_signals, "PLANNER_SIGNAL_SETTLE_SECONDS", -0.01)


@pytest.fixture
def project(tmp_path):
    made: list[Project] = []

    def make(name: str = "p", remote: str | None = None) -> Project:
        made.append(Project(tmp_path / name, remote))
        return made[-1]
    yield make
    for p in made:
        p.close()


@pytest.fixture
def farm(tmp_path, monkeypatch):
    """A control folder with `[remote]`, a code repo, and a broker serving on a free port."""
    control, repo = tmp_path / "control", tmp_path / "code"
    init_repo(repo, {"README.md": "x\n"})

    class F:
        servers, stops = [], []

        def setup(self, **over):
            control.mkdir(exist_ok=True)
            (control / "conductor.toml").write_text('project = "t"\n[repos]\ncode = "../code"\n' + remote_section(**over),
                                                    encoding="utf-8")
            self.r = settings.load(control)
            self.board = Board(self.r.state, self.r.lease_seconds, self.r.fresh_seconds, self.r.bundle_js,
                               native_argv=self.r.native)
            server = broker.make_server(self.r, self.board)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.servers.append(server)
            port = server.server_address[1]
            self.r.state.mkdir(parents=True, exist_ok=True)
            (self.r.state / "broker.json").write_text(json.dumps(
                {"pid": 0, "local": f"http://127.0.0.1:{port}/w/{self.r.secret()}/"}), encoding="utf-8")
            monkeypatch.setenv(settings.CONTROL_VAR, str(control))
            monkeypatch.chdir(repo)
            return self

        def worker(self, wid, **kw):
            stop = threading.Event()
            self.stops.append(stop)
            threading.Thread(target=worker.work, args=(client.Broker.find(self.r), wid, kw.pop("cores", 1),
                                                       self.r.fingerprint), kwargs={**kw, "stop": stop},
                             daemon=True).start()
            end = time.time() + 10
            while not any(w.id == wid for w in self.board.live()) and time.time() < end:
                time.sleep(0.02)

        def once_a_job_is_open(self, fn):
            """Run `fn` on a thread as soon as the board holds a job: a fixed timer can fire
            before a loaded box has posted it."""
            def wait():
                end = time.time() + WAIT
                while not self.board.jobs and time.time() < end:
                    time.sleep(0.01)
                fn()
            threading.Thread(target=wait, daemon=True).start()

        def run(self, first, count, *args, out="out.txt"):
            code = client.main([str(first), str(count), str(tmp_path / out), *args])
            return code, (tmp_path / out).read_bytes() if (tmp_path / out).exists() else None

        def local(self, first, count, *args):
            path = tmp_path / "local.txt"
            client.run_native(self.r.native, repo, first, count, path, list(args))
            return path.read_bytes()

        def record(self):
            return json.loads(max((self.r.state / "jobs").glob("*/job.json"), key=lambda p: p.stat().st_mtime)
                              .read_text(encoding="utf-8"))

    f = F()
    yield f
    for s in f.stops:
        s.set()
    for s in f.servers:
        s.shutdown()
