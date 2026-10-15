"""`conductor.toml` in the control folder, and the control folder's fixed layout."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAME = "conductor.toml"

# Runtime folders and files under the control folder. All git-ignored; none is ever
# committed. A request may not target anything under these.
RUNTIME = (
    "queue", "proposed", "running", "done", "reports", "questions", "requests",
    "signals", "inbox.md", "events.db", ".conductor",
)

REPO_KEYS = {"path", "worktrees", "stage", "main", "shared", "setup"}


class ConfigError(ValueError):
    pass


@dataclass
class RepoOptions:
    """Per-repo settings from a `[repos.<name>]` table; the short form `name = "path"`
    takes every default."""
    worktrees: bool = True       # false: run in the main checkout, one item at a time
    stage: bool = True           # stage the queue head while the repo is busy (needs worktrees)
    main: str = "main"           # the branch items are cut from and merged into
    shared: list[str] = field(default_factory=list)      # untracked dirs junctioned into worktrees
    setup: list[str] = field(default_factory=list)       # shell commands run in a new worktree


@dataclass
class Config:
    control: Path
    project: str
    repos: dict[str, Path]
    repo_opts: dict[str, RepoOptions] = field(default_factory=dict)
    worktree_root: Path | None = None
    locks: set[str] = field(default_factory=set)
    max_hours: float = 2.0
    poll_seconds: float = 2.0
    ask_default_minutes: float = 10.0
    stage_minutes: float = 30.0
    setup_minutes: float = 10.0
    toasts: bool = True
    command: list[str] = field(default_factory=lambda: ["claude"])
    args: list[str] = field(default_factory=list)

    # Layout ---------------------------------------------------------------
    def path(self, *parts: str) -> Path:
        return self.control.joinpath(*parts)

    @property
    def events_db(self) -> Path:
        return self.path("events.db")

    @property
    def state_dir(self) -> Path:
        return self.path(".conductor")

    def opts(self, repo: str) -> RepoOptions:
        return self.repo_opts.get(repo) or RepoOptions()

    def worktree(self, repo: str, name: str) -> Path:
        return (self.worktree_root or self.control.parent / f"{self.project}-worktrees") / repo / name

    def report_path(self, repo: str, name: str) -> Path:
        return self.path("reports", f"{repo}__{name}.md")

    def rel(self, path: Path) -> str:
        """Path relative to the control folder when inside it, else absolute; `/` separators."""
        try:
            return path.resolve().relative_to(self.control.resolve()).as_posix()
        except ValueError:
            return path.resolve().as_posix()


def check_repo_name(name: str) -> None:
    if not name or "__" in name or "/" in name or "\\" in name:
        raise ConfigError(f"{CONFIG_NAME}: repo name {name!r} may not be empty or contain '__' or slashes")


def _resolve(control: Path, value: str) -> Path:
    p = Path(value)
    return (p if p.is_absolute() else control / p).resolve()


def _repo_options(name: str, table: dict) -> RepoOptions:
    where = f"{CONFIG_NAME}: repos.{name}"
    unknown = set(table) - REPO_KEYS
    if unknown:
        raise ConfigError(f"{where}: unknown keys {sorted(unknown)}")
    opts = RepoOptions()
    for key in ("worktrees", "stage"):
        if key in table:
            if not isinstance(table[key], bool):
                raise ConfigError(f"{where}.{key} must be true or false")
            setattr(opts, key, table[key])
    if "main" in table:
        if not isinstance(table["main"], str) or not table["main"]:
            raise ConfigError(f"{where}.main must be a branch name")
        opts.main = table["main"]
    for key in ("shared", "setup"):
        value = table.get(key, [])
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list) or not all(isinstance(x, str) and x for x in value):
            raise ConfigError(f"{where}.{key} must be a list of strings")
        setattr(opts, key, value)
    for rel in opts.shared:
        p = Path(rel)
        if p.is_absolute() or ".." in p.parts:
            raise ConfigError(f"{where}.shared: {rel!r} must be a path inside the repo")
    return opts


def load(control: Path) -> Config:
    control = Path(control).resolve()
    cfg_file = control / CONFIG_NAME
    if not cfg_file.is_file():
        raise ConfigError(f"no {CONFIG_NAME} in {control}")
    try:
        data = tomllib.loads(cfg_file.read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{CONFIG_NAME}: {exc}") from exc

    project = data.get("project")
    if not isinstance(project, str) or not project:
        raise ConfigError(f"{CONFIG_NAME}: `project` (string) is required")
    raw_repos = data.get("repos")
    if not isinstance(raw_repos, dict) or not raw_repos:
        raise ConfigError(f"{CONFIG_NAME}: `[repos]` needs at least one name = path")
    repos: dict[str, Path] = {}
    repo_opts: dict[str, RepoOptions] = {}
    for name, value in raw_repos.items():
        check_repo_name(name)
        opts = RepoOptions()
        if isinstance(value, dict):
            value, opts = value.get("path"), _repo_options(name, value)
        if not isinstance(value, str):
            raise ConfigError(f"{CONFIG_NAME}: repos.{name} must be a path string or a table with `path`")
        repos[name] = _resolve(control, value)
        repo_opts[name] = opts

    root = data.get("worktree_root", f"../{project}-worktrees")
    if not isinstance(root, str) or not root:
        raise ConfigError(f"{CONFIG_NAME}: `worktree_root` must be a path string")
    worktree_root = _resolve(control, root)
    for owner in (control, *repos.values()):
        if worktree_root == owner or worktree_root.is_relative_to(owner):
            raise ConfigError(f"{CONFIG_NAME}: worktree_root {worktree_root} must be outside every repo "
                              f"and the control folder (it is inside {owner})")

    locks = data.get("locks", [])
    if not isinstance(locks, list) or not all(isinstance(x, str) for x in locks):
        raise ConfigError(f"{CONFIG_NAME}: `locks` must be a list of names")
    clash = set(locks) & set(repos)
    if clash:
        raise ConfigError(f"{CONFIG_NAME}: lock names clash with repo names: {sorted(clash)}")

    defaults = data.get("defaults", {})
    executor = data.get("executor", {})
    command = executor.get("command", ["claude"])
    if isinstance(command, str):
        command = [command]
    return Config(
        control=control,
        project=project,
        repos=repos,
        repo_opts=repo_opts,
        worktree_root=worktree_root,
        locks=set(locks),
        max_hours=float(defaults.get("max_hours", 2.0)),
        poll_seconds=float(defaults.get("poll_seconds", 2.0)),
        ask_default_minutes=float(defaults.get("ask_default_minutes", 10.0)),
        stage_minutes=float(defaults.get("stage_minutes", 30.0)),
        setup_minutes=float(defaults.get("setup_minutes", 10.0)),
        toasts=bool(defaults.get("toasts", True)),
        command=[str(x) for x in command],
        args=[str(x) for x in executor.get("args", [])],
    )
