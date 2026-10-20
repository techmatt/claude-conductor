"""`conductor init`: a new control folder with the layout in docs/control-folder.md and a first commit."""

from __future__ import annotations

import os
from pathlib import Path

from . import config as config_mod
from . import git
from .config import CONFIG_NAME, RUNTIME
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


def conductor_toml(project: str, repos: dict[str, str]) -> str:
    lines = [f"project = {_toml_str(project)}", "locks = []", "", "[defaults]",
             "max_hours = 2", "ask_default_minutes = 10", "", "[repos]"]
    lines += [f"{name} = {_toml_str(value)}" for name, value in repos.items()]
    return "\n".join(lines) + "\n"


def state_md(project: str, repos: dict[str, str]) -> str:
    return (f"# {project} — state\n\n"
            "Status, plan and flags. Changed only through conductor requests.\n\n"
            "## Repos\n\n" + "".join(f"- `{n}`: {v}\n" for n, v in repos.items()) +
            "\n## Queued and running\n\nnothing yet\n\n## Open questions\n\nnone\n")


def init(control: Path, project: str, repo_args: list[str]) -> tuple[str, list[str]]:
    """Create and commit the folder. Returns (commit sha, warnings). Never overwrites."""
    control = Path(control).resolve()
    if control.exists() and (not control.is_dir() or any(control.iterdir())):
        raise InitError(f"{control} already exists and is not empty; conductor init never overwrites")
    if not project.strip():
        raise InitError("--project must not be empty")
    if not repo_args:
        raise InitError("give at least one --repo NAME=PATH")
    repos: dict[str, str] = {}
    warnings: list[str] = []
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
        path = Path(raw).resolve()
        if not git.is_repo(path):
            warnings.append(f"repo {name}: {path} is not a git repo yet; its queue holds until it is")
        repos[name] = _repo_value(control, path)

    control.mkdir(parents=True, exist_ok=True)
    tracked = {
        CONFIG_NAME: conductor_toml(project, repos),
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
    return git.head(control), warnings
