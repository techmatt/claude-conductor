"""`conductor.toml` in the control folder, and the control folder's fixed layout."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import globs

CONFIG_NAME = "conductor.toml"

# Runtime folders and files under the control folder. All git-ignored; none is ever
# committed. A request may not target anything under these.
RUNTIME = (
    "queue", "proposed", "running", "done", "reports", "questions", "picks", "requests",
    "signals", "inbox.md", "session.md", "events.db", ".conductor",
)

REPO_KEYS = {"path", "worktrees", "stage", "main", "shared", "setup", "prose", "background"}

# Shared prose nearly every item touches: exempt from `paths`, merged textually.
DEFAULT_PROSE = ["CLAUDE.md", "README.md", "docs/**/*.md"]


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
    prose: list[str] = field(default_factory=lambda: list(DEFAULT_PROSE))   # globs outside `paths`
    background: list[str] = field(default_factory=list)  # command globs an executor always backgrounds

    def is_prose(self, path: str) -> bool:
        return any(globs.matches(path, g) for g in self.prose)


@dataclass
class RunTarget:
    """A `[run.<name>]` table: a long-running server conductor starts on request (the
    `run:` signal) and whose URL it opens in the default browser."""
    name: str
    cwd: Path
    command: list[str] | str     # a string runs through the platform shell
    url: str
    ready: str                   # the URL that must answer before `url` opens (default: url)
    ready_seconds: float = 60.0


RUN_KEYS = {"repo", "cwd", "command", "url", "ready", "ready_seconds"}

MEMORY_KEYS = ("memory_sample_seconds", "memory_keep_hours", "memory_pressure_percent",
               "memory_pressure_free_gb", "memory_critical_percent", "memory_critical_free_gb",
               "memory_calm_seconds")


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
    merge_minutes: float = 10.0      # a merge executor resolving a prose conflict
    foreground_seconds: float = 60.0  # an executor's foreground shell call moves to the background after this (0: off)
    toasts: bool = True
    # Memory (`memory.py`): sampled every N seconds (0: off); a level is commit at or over
    # a percent of its limit, or free RAM at or under a size (0 turns either off).
    memory_sample_seconds: float = 30.0
    memory_keep_hours: float = 24.0
    # Free RAM is the main signal: an auto-managed page file grows the commit limit on
    # demand, so this box sat at 87% committed through an ordinary test run (2026-10-20).
    memory_pressure_percent: float = 90.0
    memory_pressure_free_gb: float = 3.0
    memory_critical_percent: float = 95.0
    memory_critical_free_gb: float = 1.5
    memory_calm_seconds: float = 300.0   # a level falls only after the box stays clear this long
    command: list[str] = field(default_factory=lambda: ["claude"])
    args: list[str] = field(default_factory=list)
    targets: dict[str, RunTarget] = field(default_factory=dict)
    open_roots: list[Path] = field(default_factory=list)   # more folders `open:` may act in

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
    for key in ("shared", "setup", "prose", "background"):
        value = table.get(key, getattr(opts, key))
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list) or not all(isinstance(x, str) and x for x in value):
            raise ConfigError(f"{where}.{key} must be a list of strings")
        setattr(opts, key, value)
    for rel in opts.shared:
        p = Path(rel)
        if p.is_absolute() or ".." in p.parts:
            raise ConfigError(f"{where}.shared: {rel!r} must be a path inside the repo")
    for glob in opts.prose:
        if globs.invalid(glob):
            raise ConfigError(f"{where}.prose: {glob!r} must be a relative glob inside the repo")
    return opts


def _targets(control: Path, repos: dict[str, Path], raw) -> dict[str, RunTarget]:
    if raw is None:
        return {}
    if not isinstance(raw, dict) or not all(isinstance(t, dict) for t in raw.values()):
        raise ConfigError(f"{CONFIG_NAME}: `run` must be tables `[run.<name>]`")
    out = {}
    for name, table in raw.items():
        where = f"{CONFIG_NAME}: run.{name}"
        if not name or any(c in name for c in " /\\:"):
            raise ConfigError(f"{where}: a target name may not be empty or hold spaces, slashes or colons")
        if unknown := set(table) - RUN_KEYS:
            raise ConfigError(f"{where}: unknown keys {sorted(unknown)}")
        if ("repo" in table) == ("cwd" in table):
            raise ConfigError(f"{where}: give exactly one of `repo` (a repo name) or `cwd` (a folder)")
        if "repo" in table:
            if table["repo"] not in repos:
                raise ConfigError(f"{where}.repo: {table['repo']!r} is not in [repos]")
            cwd = repos[table["repo"]]
        elif isinstance(table["cwd"], str) and table["cwd"]:
            cwd = _resolve(control, table["cwd"])
        else:
            raise ConfigError(f"{where}.cwd must be a path string")
        command = table.get("command")
        if not (isinstance(command, str) and command.strip()) and not (
                isinstance(command, list) and command and all(isinstance(x, str) and x for x in command)):
            raise ConfigError(f"{where}.command must be a shell command string or a list of strings")
        urls = {}
        for key in ("url", "ready"):
            value = table.get(key, table.get("url"))
            if not isinstance(value, str) or not value.startswith(("http://", "https://")):
                raise ConfigError(f"{where}.{key} must be an http(s) URL")
            urls[key] = value
        try:
            ready_seconds = float(table.get("ready_seconds", 60))
            if ready_seconds <= 0:
                raise ValueError
        except (TypeError, ValueError):
            raise ConfigError(f"{where}.ready_seconds must be a positive number") from None
        out[name] = RunTarget(name, cwd, command, urls["url"], urls["ready"], ready_seconds)
    return out


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

    open_roots = data.get("open_roots", [])
    if not isinstance(open_roots, list) or not all(isinstance(x, str) and x for x in open_roots):
        raise ConfigError(f"{CONFIG_NAME}: `open_roots` must be a list of folder paths")

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
        merge_minutes=float(defaults.get("merge_minutes", 10.0)),
        foreground_seconds=float(defaults.get("foreground_seconds", 60.0)),
        toasts=bool(defaults.get("toasts", True)),
        **{k: float(defaults.get(k, getattr(Config, k))) for k in MEMORY_KEYS},
        command=[str(x) for x in command],
        args=[str(x) for x in executor.get("args", [])],
        targets=_targets(control, repos, data.get("run")),
        open_roots=[_resolve(control, x) for x in open_roots],
    )
