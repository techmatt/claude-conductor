"""`conductor init`: a new control folder with the layout in docs/control-folder.md and a first commit."""

from __future__ import annotations

import os
from pathlib import Path

from . import config as config_mod
from . import git
from .config import CONFIG_NAME, RUNTIME
from .session import MAINTENANCE
from .util import write_text

GITATTRIBUTES = "* text=auto eol=lf\n"


class InitError(ValueError):
    pass


def gitignore() -> str:
    lines = [f"{n}/" if "." not in n or n == ".conductor" else n for n in RUNTIME]
    return "\n".join("events.db*" if n == "events.db" else n for n in lines) + "\n"


def _toml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _repo_value(control: Path, repo: Path) -> str:
    """Relative (`../app`) when the repo sits beside or under the control folder, else
    absolute; `/` separators."""
    try:
        rel = Path(os.path.relpath(repo, control))
    except ValueError:              # different drives on Windows
        return repo.as_posix()
    return rel.as_posix() if rel.parts.count("..") <= 1 else repo.as_posix()


def conductor_toml(project: str, repos: dict[str, str], mains: dict[str, str] | None = None) -> str:
    lines = [f"project = {_toml_str(project)}", "locks = []", "", "[defaults]",
             "max_hours = 2", "ask_default_minutes = 10"]
    for name, value in repos.items():       # `push = true`: main goes to the remote after each merge
        lines += ["", f"[repos.{name}]", f"path = {_toml_str(value)}", "push = false"]
        if branch := (mains or {}).get(name):
            lines.append(f"main = {_toml_str(branch)}")
    return "\n".join(lines) + "\n"


SECTIONS = ("Queued and running", "Waiting on Matt", "Open questions", "Paged", "Pending fixes")
EMPTY = {"Queued and running": "nothing yet", "Waiting on Matt": "nothing"}


def state_md(project: str, repos: dict[str, str]) -> str:
    """Every section planner.md expects, in its order, so the planner's first request is edits:
    Repos, the item and Matt sections, Maintenance (every template never, per repo), Planner habits."""
    never = "; ".join(f"{word} never" for _, word in MAINTENANCE)
    out = [f"# {project} — state", "", "Status, plan and flags. Changed only through conductor requests.", "",
           "## Repos", "", *(f"- `{n}`: {v}" for n, v in repos.items())]
    for name in SECTIONS:
        out += ["", f"## {name}", "", EMPTY.get(name, "none")]
    out += ["", "## Maintenance", "", *(f"- `{n}`: {never}; workflow retro never." for n in repos),
            "- Triggers: planner.md's defaults.", "", "## Planner habits", ""]
    return "\n".join(out) + "\n"


def prepare_repo(name: str, path: Path) -> tuple[str | None, str | None]:
    """Make `path` a repo an item can run in: create the folder, `git init -b main` and an
    empty first commit as needed (no remote). Returns (what was done or None, the `main`
    to write when it isn't `main`). Raises InitError when git refuses."""
    try:
        if path.exists() and not path.is_dir():
            raise InitError(f"repo {name}: {path} is a file")
        made = not path.exists()
        if made or git.toplevel(path) != path:
            path.mkdir(parents=True, exist_ok=True)
            git.run(path, "init", "-q", "-b", "main")
            git.run(path, "commit", "-q", "--allow-empty", "-m", "Empty first commit (conductor init)")
            return (f"repo {name}: created {path} (git init -b main, one empty commit, no remote)" if made
                    else f"repo {name}: made {path} a git repo (git init -b main, one empty commit, no remote)"), None
        if git.head(path) is None:          # a repo with no commits: its unborn branch becomes main
            git.run(path, "symbolic-ref", "HEAD", "refs/heads/main")
            git.run(path, "commit", "-q", "--allow-empty", "-m", "Empty first commit (conductor init)")
            return f"repo {name}: {path} had no commits; made one empty commit on main", None
        branch = (git.symbolic_head(path) or "").removeprefix("refs/heads/")
        if git.rev(path, "refs/heads/main") or not branch or branch == "main":
            return None, None
        return f"repo {name}: its branch is {branch}, written as main = \"{branch}\"", branch
    except git.GitError as exc:
        raise InitError(f"repo {name}: {exc}") from exc


def init(control: Path, project: str, repo_args: list[str]) -> tuple[str, list[str]]:
    """Create and commit the folder, first creating any code repo that is missing or has no
    commits. Returns (commit sha, what was created or adjusted). Never overwrites."""
    control = Path(control).resolve()
    if control.exists() and (not control.is_dir() or any(control.iterdir())):
        raise InitError(f"{control} already exists and is not empty; conductor init never overwrites")
    if not project.strip():
        raise InitError("--project must not be empty")
    if not repo_args:
        raise InitError("give at least one --repo NAME=PATH")
    repos: dict[str, str] = {}
    paths: dict[str, Path] = {}
    for arg in repo_args:
        name, sep, raw = arg.partition("=")
        name, raw = name.strip(), raw.strip()
        if not sep or not raw:
            raise InitError(f"--repo {arg!r}: expected NAME=PATH")
        try:
            config_mod.check_repo_name(name)
        except config_mod.ConfigError as exc:
            raise InitError(str(exc)) from exc
        if name in repos:
            raise InitError(f"repo {name!r} given twice")
        paths[name] = path = Path(raw).resolve()
        if path == control or control in path.parents:
            raise InitError(f"repo {name}: {path} is inside the control folder")
        repos[name] = _repo_value(control, path)

    notes: list[str] = []
    mains: dict[str, str] = {}
    for name, path in paths.items():
        note, branch = prepare_repo(name, path)
        notes += [note] if note else []
        if branch:
            mains[name] = branch
        if git.has_remote(path) is False:
            notes.append(git.NO_REMOTE.format(repo=name) + " (session.md says so until it has one)")

    control.mkdir(parents=True, exist_ok=True)
    tracked = {
        CONFIG_NAME: conductor_toml(project, repos, mains),
        "state.md": state_md(project, repos),
        ".gitignore": gitignore(),
        ".gitattributes": GITATTRIBUTES,
    }
    for rel, text in tracked.items():
        write_text(control / rel, text)
    for d in ("queue", "proposed", "running", "done", "reports", "questions", "picks", "requests", "signals"):
        (control / d).mkdir()
    for name in repos:
        (control / "queue" / name).mkdir()
    config_mod.load(control)        # what was written must load
    git.run(control, "init", "-q", "-b", "main")
    git.run(control, "add", "--", *tracked)
    git.run(control, "commit", "-q", "-m", f"conductor init: {project}", "--", *tracked)
    return git.head(control), notes
