"""Resume items: `resume: <repo>/<NNN_name>` puts Matt's questions (or `template: self_audit`)
to a finished item's own session. The session is forked (`--resume <orig> --fork-session
--session-id <new>`), so the original stays as it was and can be resumed again; the item
runs readonly in a fresh worktree at current main, never merges, and may read the
original's record in the control folder (named in its preamble).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import git
from .config import Config
from .events import EventLog
from .util import clock, read_text, write_text

CONFIG_ENV = "CLAUDE_CONFIG_DIR"          # where Claude Code keeps `projects/<folder>/<session>.jsonl`
SINCE_LIMIT = 50                         # `git.commits_between` returns at most this many

RESUMED = """
Resumed. This session is your own run of {orig}, resumed as a fork: you remember that run,
and its session stays as it was. This is a new item, `{item_id}`: readonly, never merged,
and its prompt below is about that run. Your report this time goes to the path named
below, not the one from your run. The code may have moved on: your working directory is
fresh at `{main}` {base}, and {since}
Your own record. You may read these files in conductor's control folder, the one
exception to the rule below against reading that folder:
{files}
Answer each question by number."""


def label(d: dict) -> str:
    """How every view names a resume item (its event detail, run record or `meta()`):
    `post-audit of <orig>` for a self-audit, `resume of <orig>` otherwise; "" for others."""
    if not d.get("resume_of"):
        return ""
    return f"{'post-audit' if d.get('template') == 'self_audit' else 'resume'} of {d['resume_of']}"


def problem(view, item) -> str | None:
    """Why `item`'s `resume:` cannot run (yet), from the queue and the event log, or None.
    `view` is a `state.View`: only a finished item with a real session can be resumed."""
    orig = item.resume_of
    if orig in view.running or orig in view.staged:
        return f"`resume`: {orig} is {'running' if orig in view.running else 'staged'}; only a finished item can be resumed"
    if any(i.id == orig for q in view.queued.values() for i in q):
        return f"`resume`: {orig} is queued; only a finished item can be resumed"
    fin = view.finishes.get(orig)
    if fin is None:
        return f"`resume`: no finished item {orig}"
    if fin.detail.get("status") not in ("done", "partial", "red"):
        return f"`resume`: {orig} ended {fin.detail.get('status')}"
    reasons = fin.detail.get("reasons") or []
    if not fin.detail.get("session_id") or any(str(r).startswith("launch failed") for r in reasons):
        return f"`resume`: {orig} has no session to resume (its finish names none, or it never launched)"
    return None


def session_file(session_id: str) -> Path | None:
    """The transcript Claude Code keeps for a session, or None. Checked before a fork is
    launched: `--resume <missing> --fork-session --session-id <new>` exits 0 and silently
    starts a fresh session (Claude Code 2.1.306), which would answer from nothing."""
    root = Path(os.environ.get(CONFIG_ENV) or Path.home() / ".claude") / "projects"
    try:
        return next(root.glob(f"*/{session_id}.jsonl"), None)
    except OSError:
        return None


def original_run(cfg: Config, orig: str) -> dict:
    """The original's run record from `done/`, or {}."""
    repo, _, name = orig.partition("/")
    try:
        data = json.loads(read_text(cfg.path("done", repo, f"{name}.run.json")))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, UnicodeDecodeError):
        return {}


def prepare(cfg: Config, log: EventLog, rec: dict) -> str | None:
    """At launch: the original's session (into `rec["resume_session"]`) and where its code
    stood (`rec["resume_since"]`), or the error that makes the launch fail."""
    orig = rec["resume_of"]
    fin = log.last("finish", item=orig)
    sid = fin.detail.get("session_id") if fin else None
    if not sid:
        return f"resume: {orig} has no finished session"
    if session_file(sid) is None:
        return f"resume: {orig}'s session {sid} has no transcript in Claude Code's projects folder"
    run = original_run(cfg, orig)
    rec["resume_session"] = sid
    on_main = not run.get("worktree") and not run.get("readonly")     # `worktrees = false`: it committed to main
    if fin.detail.get("merged"):
        rec["resume_since"] = fin.detail["merged"]
    elif on_main and run:
        last = log.last("commit", item=orig)
        rec["resume_since"] = last.detail.get("sha") if last else run.get("repo_head")
    else:
        rec["resume_since"] = run.get("base") or run.get("repo_head")
    rec["resume_merged"] = bool(fin.detail.get("merged")) or (on_main and bool(run))
    return None


def readable(cfg: Config, orig: str, extract: Path) -> list[Path]:
    """What a resume item may read of its original's record: its prompt, report, audit
    notes, run log and stage log (those that exist), and the events extract."""
    from .executor import RunPaths
    repo, _, name = orig.partition("/")
    p = RunPaths.for_name(cfg, repo, name)
    return [f for f in (p.prompt, p.report, p.audit, p.log, p.stage_log) if f.is_file()] + [extract]


def write_extract(log: EventLog, orig: str, path: Path) -> None:
    """The original's events, one line each, beside the resume item's prompt."""
    lines = [f"# Events of {orig}", ""]
    for ev in log.query(item=orig):
        if ev.item != orig:
            continue
        detail = json.dumps(ev.detail, ensure_ascii=False, default=str)
        lines.append(f"- #{ev.id} {clock(ev.ts)} `{ev.kind}` {detail}")
    write_text(path, "\n".join(lines) + "\n")


def paragraph(cfg: Config, log: EventLog, rec: dict, extract: Path) -> str:
    """The preamble's *Resumed* paragraph: what merged since the original, and its record."""
    orig, repo = rec["resume_of"], rec["repo"]
    main, base = cfg.opts(repo).main, rec.get("base") or rec.get("repo_head") or ""
    since_sha = rec.get("resume_since")
    landed = git.commits_between(cfg.repos[repo], since_sha, base) if since_sha and base else []
    if not since_sha:
        since = "conductor could not tell where your run's code stood; check `git log` yourself."
    elif not landed:
        since = "nothing has landed on it since your run" + (" merged." if rec.get("resume_merged") else ".")
    else:
        what = "your run merged" if rec.get("resume_merged") else "your run started (it never merged)"
        more = f" (the newest {SINCE_LIMIT}; `git log {since_sha[:10]}..{base[:10]}` for all)" \
            if len(landed) >= SINCE_LIMIT else ""
        since = (f"these landed since {what}, at {since_sha[:10]}{more}:\n"
                 + "\n".join(f"- {sha[:10]} {subject}" for sha, subject in reversed(landed)))
    write_extract(log, orig, extract)
    files = "\n".join(f"    {p.resolve()}" for p in readable(cfg, orig, extract))
    return RESUMED.format(orig=orig, item_id=rec["item"], main=main, base=base[:10], since=since, files=files)
