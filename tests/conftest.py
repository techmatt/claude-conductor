import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conductor import config, daemon, request

FAKE = Path(__file__).with_name("fake_claude.py")

CONTROL_GITIGNORE = """\
queue/
proposed/
running/
done/
reports/
questions/
picks/
requests/
signals/
inbox.md
session.md
events.db*
.conductor/
"""


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


def init_repo(path: Path, files: dict[str, str]) -> None:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.name", "test")
    git(path, "config", "user.email", "test@example.com")
    for name, text in files.items():
        (path / name).write_text(text, encoding="utf-8", newline="\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "init")


def build_template(root: Path) -> Path:
    """The code repo and control folder every `Env` starts from: built once per test
    process and copied per test, since each git call costs ~30ms on Windows."""
    init_repo(root / "code", {"README.md": "code\n"})
    toml = (
        'project = "test"\nlocks = ["gpu"]\n\n[defaults]\nmax_hours = 1\npoll_seconds = 0.1\n'
        'ask_default_minutes = 0.03\n'
        # The box's real memory never gates a test; memory tests set a probe or thresholds.
        'memory_pressure_percent = 0\nmemory_pressure_free_gb = 0\n'
        'memory_critical_percent = 0\nmemory_critical_free_gb = 0\n\n'
        f'[repos]\ncode = "../code"\n\n[executor]\ncommand = {[sys.executable, str(FAKE)]!r}\n'
    ).replace("'", '"').replace("\\", "/")
    init_repo(root / "control", {"conductor.toml": toml, "state.md": "# state\n",
                                 ".gitignore": CONTROL_GITIGNORE, ".gitattributes": "* text=auto eol=lf\n"})
    return root


class Env:
    def __init__(self, root: Path, template: Path):
        self.root = root
        self.control = root / "control"
        self.repo = root / "code"
        for name in ("code", "control"):
            shutil.copytree(template / name, root / name)
        self.cfg = config.load(self.control)
        self.lines: list[str] = []
        self.toasts: list[tuple[str, str, str]] = []
        self.d = self.new_daemon()

    def notifier(self, title: str, body: str, tag: str) -> None:
        self.toasts.append((title, body, tag))

    def new_daemon(self) -> daemon.Daemon:
        d = daemon.Daemon(config.load(self.control), echo=self.lines.append, notifier=self.notifier)
        d.startup()
        return d

    def configure(self, repo_table: str) -> None:
        """Replace `code = "../code"` with a `[repos.code]` table and restart the daemon."""
        toml = self.control / "conductor.toml"
        text = toml.read_text(encoding="utf-8").replace('[repos]\ncode = "../code"\n', repo_table)
        toml.write_text(text, encoding="utf-8", newline="\n")
        git(self.control, "commit", "-qam", "configure")
        self.d.log.close()
        self.d = self.new_daemon()

    def worktree(self, name: str) -> Path:
        return self.d.cfg.worktree("code", name)

    def queue(self, name: str, body: str, repo: str = "code", **meta) -> Path:
        fm = "\n".join(f"{k}: {v}" for k, v in {"repo": repo, **meta}.items())
        p = self.control / "queue" / repo / f"{name}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"---\n{fm}\n---\n{body}\n", encoding="utf-8")
        return p

    def hold(self) -> str:
        """A FAKE line that keeps an executor running until `release()`: a test holds an
        item exactly as long as it needs, not a fixed sleep that races a loaded machine."""
        return f"FAKE wait-for {(self.root / 'go').as_posix()} 60"

    def release(self) -> None:
        (self.root / "go").touch()

    def signal(self, name: str, text: str) -> None:
        (self.control / "signals" / name).write_text(text, encoding="utf-8")

    def tick_until(self, cond, timeout: float = 30) -> None:
        end = time.time() + timeout
        while time.time() < end:
            self.d.tick()
            if cond():
                return
            time.sleep(0.05)
        raise AssertionError("timed out; events:\n" + "\n".join(self.lines))

    def events(self, kind: str) -> list:
        return self.d.log.query(kinds=[kind])

    def finished(self, item: str):
        return self.d.log.last("finish", item=item)


@pytest.fixture(autouse=True)
def no_claude_ancestor(monkeypatch):
    """The suite often runs inside a Claude Code session: a daemon's warning about that
    is tested on its own, never left to whoever runs the tests."""
    from conductor import procs
    monkeypatch.setattr(procs, "claude_ancestor", lambda: None)


@pytest.fixture(scope="session")
def env_template(tmp_path_factory) -> Path:
    return build_template(tmp_path_factory.mktemp("template"))


@pytest.fixture
def env(tmp_path, monkeypatch, env_template):
    monkeypatch.setattr(request, "SETTLE_SECONDS", 0)
    monkeypatch.setattr(daemon, "SIGNAL_SETTLE_SECONDS", 0)
    e = Env(tmp_path, env_template)
    yield e
    for run in list(e.d.runs.values()):
        if run.pid():
            from conductor import procs
            procs.kill_tree(run.pid())
    e.d.log.close()
