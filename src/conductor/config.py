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


class ConfigError(ValueError):
    pass


@dataclass
class Config:
    control: Path
    project: str
    repos: dict[str, Path]
    locks: set[str] = field(default_factory=set)
    max_hours: float = 2.0
    poll_seconds: float = 2.0
    ask_default_minutes: float = 10.0
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
    for name, value in raw_repos.items():
        if not isinstance(value, str):
            raise ConfigError(f"{CONFIG_NAME}: repos.{name} must be a path string")
        check_repo_name(name)
        p = Path(value)
        repos[name] = (p if p.is_absolute() else control / p).resolve()

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
        locks=set(locks),
        max_hours=float(defaults.get("max_hours", 2.0)),
        poll_seconds=float(defaults.get("poll_seconds", 2.0)),
        ask_default_minutes=float(defaults.get("ask_default_minutes", 10.0)),
        toasts=bool(defaults.get("toasts", True)),
        command=[str(x) for x in command],
        args=[str(x) for x in executor.get("args", [])],
    )
