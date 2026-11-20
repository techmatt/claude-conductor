"""Executor memory notes: Claude Code's per-repo memory under `~/.claude/projects/<repo
folder>/memory/`, changed by the daemon on the planner's request (143; docs/memory-notes.md).

The hook refuses every executor write there (`rules.home_claude`), and Matt doesn't edit by
hand, so before 143 a note an audit ruled wrong had no route (fractal-wallpapers'
`final-statement-ends-with-report-filename`, flagged by 060, 062 and 063). Now the planner's
`memory_note` tool writes a request, `memory-edits/<stamp>.md`:

    ---
    repo: <repo>
    note: <slug>              the note's file name, without `.md`
    op: write | edit | delete
    description: <one line>   write: the index line's hook (MEMORY.md), and the note's own
    edits: [{old, new}, ...]  edit: each `old` a unique substring of the note now
    ---
    <a write's whole note>

and the daemon applies it (`apply`), logs `memory_note` (or `memory_refused`), writes the
receipt and deletes the request. `memory-edits/` is runtime and ignores itself.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .util import FrontMatterError, read_text, retry_io, split_front_matter, write_text

ROOT = "memory-edits"
INDEX = "MEMORY.md"
OPS = ("write", "edit", "delete")
NOTE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class NoteRefused(ValueError):
    """A request not carried out (nothing written): the text says why."""


def prepare(control: Path) -> None:
    root = Path(control) / ROOT
    root.mkdir(parents=True, exist_ok=True)
    if not (root / ".gitignore").is_file():
        write_text(root / ".gitignore", "*\n")


def folder_name(repo_path: Path | str) -> str:
    """Claude Code's folder for a working directory: each character not a letter or digit
    becomes `-` (`C:\\Code\\fractal-wallpapers` → `C--Code-fractal-wallpapers`)."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(repo_path))


def memory_dir(repo_path: Path | str) -> Path:
    """The repo's memory folder. An item's worktree session shares its repo's (Claude Code
    keys memory by the main checkout)."""
    from .resume import CONFIG_ENV
    root = Path(os.environ.get(CONFIG_ENV) or Path.home() / ".claude")
    return root / "projects" / folder_name(repo_path) / "memory"


def parse(text: str) -> tuple[dict, str]:
    try:
        meta, body = split_front_matter(text)
    except FrontMatterError as exc:
        raise NoteRefused(f"front-matter: {exc}") from None
    if not isinstance(meta, dict):
        raise NoteRefused("no front-matter (repo, note, op)")
    return meta, body


def substitute(text: str, old: str, new: str, where: str) -> str:
    """`old` replaced by `new` in `text`, where `old` occurs exactly once; else refused."""
    if not old:
        raise NoteRefused(f"{where}: `old` is empty")
    n = text.count(old)
    if n != 1:
        raise NoteRefused(f"{where}: `old` occurs {n} times in the note; give a longer, unique piece"
                           if n else f"{where}: `old` is not in the note now")
    return text.replace(old, new, 1)


def _title(slug: str) -> str:
    words = slug.replace("_", "-").split("-")
    return " ".join(words).capitalize()


def _index(mem: Path, slug: str, description: str | None, drop: bool) -> str:
    """MEMORY.md with `slug`'s line dropped, or added / replaced when `description` is given."""
    p = mem / INDEX
    try:
        lines = read_text(p).splitlines()
    except (OSError, UnicodeDecodeError):
        lines = []
    link = f"({slug}.md)"
    at = next((i for i, ln in enumerate(lines) if link in ln), None)
    if drop:
        if at is not None:
            del lines[at]
    elif description:
        line = f"- [{_title(slug)}]({slug}.md) — {description}"
        if at is None:
            lines.append(line)
        else:
            lines[at] = line
    else:
        return ""
    retry_io(write_text, p, "\n".join(lines).rstrip("\n") + "\n" if lines else "")
    return f"; {INDEX} {'line dropped' if drop else 'line set'}"


def apply(repos: dict[str, Path], meta: dict, body: str) -> dict:
    """Carry out one request; returns the event detail (`repo`, `note`, `op`, `path`,
    `result`). Refused with `NoteRefused`."""
    repo, slug = str(meta.get("repo") or ""), str(meta.get("note") or "").removesuffix(".md")
    op = str(meta.get("op") or "").lower()
    if repo not in repos:
        raise NoteRefused(f"`repo: {repo}` is not a configured repo ({', '.join(repos)})")
    if not NOTE_RE.match(slug):
        raise NoteRefused(f"`note: {slug}`: a file name without `.md` (letters, digits, _ . -)")
    if op not in OPS:
        raise NoteRefused(f"`op: {op}`: one of {', '.join(OPS)}")
    mem = memory_dir(repos[repo])
    path = mem / f"{slug}.md"
    try:
        current = read_text(path)
    except FileNotFoundError:
        current = None
    except (OSError, UnicodeDecodeError) as exc:
        raise NoteRefused(f"{path} doesn't read: {exc}") from None
    desc = " ".join(str(meta.get("description") or "").split()) or None
    if op == "delete":
        if current is None:
            raise NoteRefused(f"no note {slug} for {repo}; notes: {', '.join(names(mem)) or 'none'}")
        path.unlink()
        said = "deleted" + _index(mem, slug, None, drop=True)
    elif op == "edit":
        if current is None:
            raise NoteRefused(f"no note {slug} for {repo}; notes: {', '.join(names(mem)) or 'none'}")
        edits = meta.get("edits") or []
        if not isinstance(edits, list) or not edits:
            raise NoteRefused("an edit needs `edits`: [{old, new}, ...]")
        text = current
        for n, e in enumerate(edits, 1):
            text = substitute(text, str((e or {}).get("old") or ""), str((e or {}).get("new") or ""), f"edit {n}")
        retry_io(write_text, path, text)
        said = f"{len(edits)} edit(s)" + _index(mem, slug, desc, drop=False)
    else:
        if not body.strip():
            raise NoteRefused("a write needs the note's text as the body")
        if current is None and not desc:
            raise NoteRefused("a new note needs `description`: its MEMORY.md line, without which no session loads it")
        mem.mkdir(parents=True, exist_ok=True)
        retry_io(write_text, path, body.rstrip("\n") + "\n")
        said = ("rewritten" if current is not None else "written") + _index(mem, slug, desc, drop=False)
    return {"repo": repo, "note": slug, "op": op, "path": str(path), "result": said}


def names(mem: Path) -> list[str]:
    try:
        return sorted(p.stem for p in mem.glob("*.md") if p.name != INDEX)
    except OSError:
        return []


def take(control: Path) -> list[Path]:
    """The requests waiting, oldest name first (a `.tmp` is still being written)."""
    try:
        return sorted(p for p in (Path(control) / ROOT).glob("*.md"))
    except OSError:
        return []
