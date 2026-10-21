"""Executors: the preamble, the `claude -p` command line, and reading what a run left."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import audits
from .config import Config
from .items import Item
from .util import clock, read_text

PREAMBLE = """\
You are a conductor executor: an unattended headless Claude Code session running queue
item {item_id} for project {project}. Nobody is watching the session.

Asking. `ask` (mcp__conductor__ask) puts a question to Matt. Ask only when a wrong guess
would waste real work or be hard to undo. Give a default whenever a reasonable one
exists: an unanswered question returns its default after {ask_minutes:g} minutes and you
carry on with it. Block (`block: true`) only when every default would be worse than
waiting; a blocking question waits until answered, and that time does not count toward
your time limit. Everything else is still a branch: do what fits the goal, record what
didn't, and continue. The only hard stop is a wrong-repo guard in the prompt.

Progress. `progress` (mcp__conductor__progress) shows Matt one short line about how
this item is going and returns at once. Call it at the start, with a first `eta` as soon
as you can estimate one; at real milestones; and whenever your estimate changes
materially. Not chatter.

Outline. Once you've oriented and have a plan, call `progress` with `outline`: your
sections in order, the report last, at most ~8 rows, each a few words with a rough local
finish time (11:05am). Post it again whenever a message asks for one. When a section
finishes, resend the outline with that row `done` in your next progress call; leave the
other times alone unless they moved a lot. Keep it coarse: no descriptions, and no
effort spent making it accurate. Staged, your plan ends with it: post it first thing.

Messages. Matt, the operator, may message you while you work through conductor's
message channel: hook context after one of your own tool calls, marked MESSAGE #n from
Matt, or AMENDMENT #n from Matt for an addition to the prompt. It is not tool-output
injection: it is Matt's instruction and outranks the prompt where they conflict. Answer
it with `progress`. Tell any helper or subagent you start that if it sees such a
delivery it passes it straight up to you, word for word; act on what it relays.
{calls}
{workdir}{readonly}{paths}{port}{beside}
{audit}
Time limit: {max_hours:g}h. Conductor hard-kills this session at {deadline}, later by any
time spent blocked on a question.

When you finish, whatever the outcome, write your report to exactly this path:
    {report}
{only} Do not read,
write or commit anything else in that folder: conductor checks it after you exit,
reverts any change, and marks this item red.

The report must start with these four lines:
status: done | partial | red
blocks: <what this blocks, or none>
carry: <what state.md should change, or none>
slow: none | <component> ~<time>, <why>, <what would fix it>[; <another>]
and, only if you wrote a picker manifest, a fifth:
pick: <manifest path relative to the repo root>
`slow:` names only a part of this work that took over ~15 minutes, or about twice what
you expected: a coarse time, the cause, and a fix if you can see one (a test-lane split,
a fact missing from README or CLAUDE.md, a slow build step, a tool that kept failing,
too much searching to find something). `none` is the normal answer; never pad it, and
skip timings your progress lines already gave.
`done`: the goal was met.
`partial`: part of the goal was met, nothing is broken, and the rest is follow-up work.
This includes declining an instruction for a stated reason.
`red`: something is broken or unsafe, and this repo's queue should not continue without
a look.

{pick}
The report answers every amendment and message by number: done, partial or not done,
and why. It carries test details only for a red you could not fix, and no process
narration. The rest of the report follows the repo's CLAUDE.md. Your final message ends with
`Report written to {report}`.

{tail}"""

PICK = """\
Pickers. When the prompt asks for a picker, or you produce candidates for Matt to choose
between (art variants, layouts, anything visual), write a picker manifest and add the
`pick:` line to the report header. Matt picks one option per row in his browser and adds
notes; the next prompt carries his choices. The manifest is a JSON file in this repo at
a path that survives the merge (committed, or inside a shared folder), with its images
kept the same way:
    {"name": "art_alts_009",
     "title": "Hero art alternatives",
     "intro": "optional text above the rows",
     "rows": [{"id": "hero", "label": "Hero, idle pose", "note": "optional",
               "options": [{"key": "current", "image": "hero/current.png"},
                           {"key": "1", "image": "hero/alt1.png", "label": "warmer"}]}]}
`name` is a slug (lowercase letters, digits, `_` or `-`), unique in the project. The first
option in each row is the current one and the default. `key` is what the next prompt
will use for the choice; keys are unique within a row and row ids within the file.
`image` is relative to the manifest, must exist inside the repo, and is PNG, JPEG, GIF,
WebP, AVIF or SVG. `intro`, `note` and an option's `label` are optional.
"""

CALLS = """
Short calls. A message can only get in between tool calls, so no call should last more
than about {cap:g} seconds. Run anything that may take longer (tests, builds, renders) with
`run_in_background`, and wait on it in polls of at most {cap:g} seconds each (a `timeout`
of {cap_ms} or less), never one long waiter. Do not end your turn to wait for it either:
a message cannot reach you while you are idle. Conductor enforces this: a foreground
shell call still running after {cap:g} seconds is moved to the background (you are told
so and notified when it ends; Claude Code kills it 10 minutes after the move){bg}.
"""

CALLS_BG = ", and commands matching {globs} always run in the background"

PROMPT_FOLLOWS = "The prompt follows.\n\n---\n\n"

ONLY_ONE = "That file is the only thing you may write outside the working directory."
ONLY_TWO = ("That file and the audit notes file named above are the only things you may write "
            "outside the working directory.")

WORKDIR_MAIN = "Your working directory is the code repo `{repo}` ({repo_path})."

WORKDIR_TREE = """\
Your working directory is a git worktree of the code repo `{repo}`: {cwd}, on branch
`{branch}`, cut from `{main}` at {base}. Commit your work to this branch and leave
nothing uncommitted. Never check out, commit to, rebase or push `{main}`, and never
touch the main checkout at {repo_path}. When you finish, conductor rebases this branch
onto `{main}` and fast-forwards `{main}` to it; uncommitted changes make the item red.{shared}"""

SHARED = ("\nShared with the main checkout (linked, not copied — a change there is a change "
          "everywhere): {names}.")

READONLY = ("\nThis item is readonly: commit nothing and change no tracked file. "
            "Conductor checks the repo afterwards and marks a change red.")

PATHS = ("\nThis item declared `paths:` {globs}. Change nothing outside them: conductor "
         "checks what the branch changed and marks anything else red, unmerged.{prose}")

PORT_VAR = "CONDUCTOR_PORT"

PORT = ("\nPort. Any server you start (the app, a dev server, anything that listens) uses port "
        "{port}, also in your environment as `" + PORT_VAR + "`; never the repo's usual port, which "
        "a run target or another item's server may hold. Where the repo's docs or scripts "
        "hard-code a port, pass this one through (a flag or an environment variable) without "
        "changing them. If you need a second port, ask the OS for a free one (bind to port 0).")

# Warnings when an item starts beside others in its repo and either is `alongside`: a
# paragraph in its prompt, and a message (the `tell:` path) to each one already running.
BESIDE = ("\nBeside you. Conductor started you beside other items in this repo, each in its own "
          "worktree and merging on its own:\n{items}\nThey are likely unrelated to yours: stay out "
          "of their areas, and say in your report if you had to touch them.")
BESIDE_TELL = ("{item} started beside you: {gist}. Likely unrelated; avoid those files, and say in your "
               "report if you had to touch them.")

PROSE = (" Prose files ({globs}) are exempt: update them as the work needs; another item may "
         "edit them at the same time, and conductor merges them textually.")

STAGE = """\
You are a conductor executor in its staging phase: an unattended headless Claude Code
session for queue item {item_id} of project {project}. Nobody is watching the session.
Another item holds this repo's lock, so you get a head start while you wait.

{workdir}

While staging:
- read the prompt below and whatever it points at;
- run read-only commands only: nothing that writes a file, commits, installs or deletes;
- nothing long and nothing that holds heavy resources: no builds, no test suites, no
  servers, no GPU work; at most {stage_minutes:g} minutes in all;
- do not ask questions now (there is no ask tool yet); note them in your plan.

Then write your plan for executing the prompt as your final message, ending with its
outline: the sections you will work through, a few words each, the report last. Stop. When
the lock is yours, conductor resumes this same session in this same worktree, moved to
the then-current `{main}`, with the instructions for executing.

The prompt follows.

---

"""

RESUME_HEAD = """\
You have the lock now. Conductor moved your worktree from {old} to the current `{main}`
at {new}{landed}. Check what landed since you staged and adjust your plan, then execute
the prompt you read while staging. Everything below now applies.

"""

AMEND = """

---

## Amendment #{id} ({when}{by}): it amends the prompt above and wins where they conflict

{text}
"""

RESUME_AMENDS = """
---

Amendments. Matt added to the prompt after you read it. Each amends the prompt you read
while staging, and where it conflicts with the plan you made, it overrides that plan.
"""

RESUME_AMEND = """
## Amendment #{id} ({when}{by})

{text}
"""


MERGE = """\
You are a conductor merge executor: an unattended headless Claude Code session for
project {project}. Nobody is watching. Your one job is to resolve a textual conflict in
prose files, then stop.

Your working directory is the worktree of item {item_id} ({cwd}), stopped part-way
through rebasing its branch `{branch}` onto `{main}`. These files hold conflict markers:
{files}

In each conflict block, the side between `<<<<<<<` and `=======` is what is on `{main}`
now (other work that landed meanwhile); the side between `=======` and `>>>>>>>` is
this item's change. Replace every block with text that keeps the intent of both sides:
keep both sides' additions, and where both rewrote the same lines, combine them so
neither change is lost. Leave no conflict marker.

Touch nothing else: no other file, no git command (no add, commit, rebase, checkout,
stash), no build, no tests. Conductor checks the files, stages them and continues the
rebase. If the two sides truly cannot be reconciled, leave the markers in place and say
why in your final message: the item then ends red with both versions kept.

For intent only. Commits on `{main}` since the item was cut:
{upstream}
The item's commit being replayed: {commit}
The item's prompt, which you must not carry out:

---

{body}"""


def build_merge_prompt(cfg: Config, item_id: str, run: dict, files: list[str], upstream: list[tuple[str, str]],
                       commit: str, body: str) -> str:
    repo = run["repo"]
    ups = "\n".join(f"- {sha[:10]} {subject}" for sha, subject in upstream) or "- (none listed)"
    return MERGE.format(project=cfg.project, item_id=item_id, cwd=run["worktree"], branch=run["branch"],
                        main=cfg.opts(repo).main, files="\n".join(f"- {f}" for f in files), upstream=ups,
                        commit=commit or "(unknown)", body=body.strip() + "\n")


def _ticks(names: list[str]) -> str:
    return ", ".join(f"`{n}`" for n in names)


def _workdir(cfg: Config, item: Item, run: dict) -> str:
    if not run.get("worktree"):
        return WORKDIR_MAIN.format(repo=item.repo, repo_path=cfg.repos[item.repo])
    shared = SHARED.format(names=", ".join(f"`{s}`" for s in run["shared"])) if run.get("shared") else ""
    return WORKDIR_TREE.format(repo=item.repo, cwd=run["worktree"], branch=run["branch"],
                               main=cfg.opts(item.repo).main, base=run["base"][:10],
                               repo_path=cfg.repos[item.repo], shared=shared)

# Per-server tool timeout for the ask server: Claude Code otherwise aborts a call silent
# for 30 minutes. Seven days in ms, under 2**31 ms where a JavaScript timer would wrap.
# The ask server's progress notifications cover the same limit independently.
ASK_TIMEOUT_MS = 7 * 24 * 3600 * 1000

HEADER_KEYS = ("status", "blocks", "carry")
STATUSES = ("done", "partial", "red")


@dataclass
class RunPaths:
    report: Path
    log: Path          # stream-json stdout, the raw output
    stderr: Path
    prompt: Path       # exactly what was sent on stdin
    mcp: Path          # the --mcp-config naming the ask server
    settings: Path     # the --settings holding the message hook
    setup: Path        # output of the repo's setup commands
    stage_log: Path    # the staging phase's stream-json, stderr and prompt
    stage_stderr: Path
    stage_prompt: Path
    merge_log: Path    # a merge executor resolving a prose conflict: stream-json, stderr, prompt
    merge_stderr: Path
    merge_prompt: Path
    audit: Path        # the audit notes an `audit: true` or general-audit item keeps
    amend: Path        # every amendment delivered to the item, in order, with times

    @classmethod
    def for_item(cls, cfg: Config, item: Item) -> "RunPaths":
        return cls.for_name(cfg, item.repo, item.name)

    @classmethod
    def for_name(cls, cfg: Config, repo: str, name: str) -> "RunPaths":
        report = cfg.report_path(repo, name)
        base = report.with_suffix("")
        def sib(suffix: str) -> Path:
            return base.with_name(base.name + suffix)
        return cls(report, sib(".log"), sib(".stderr.log"), sib(".prompt.md"), sib(".mcp.json"),
                   sib(".settings.json"), sib(".setup.log"), sib(".stage.log"), sib(".stage.stderr.log"), sib(".stage.prompt.md"),
                   sib(".merge.log"), sib(".merge.stderr.log"), sib(".merge.prompt.md"), sib(".audit.md"),
                   sib(".amend.md"))


def build_prompt(cfg: Config, item: Item, paths: RunPaths, max_hours: float, deadline: datetime,
                 run: dict | None = None, resume: dict | None = None, notes: str = "",
                 usage: str = "", amends: list[dict] = (), beside: list[tuple[str, str]] = ()) -> str:
    """The execution preamble and the item body; with `resume` (old/new base, landed
    count), the message that resumes a staged session instead. `notes`: a cleanup's
    audit notes, inlined; `usage`: a disk audit's control-folder sizes and worktrees;
    `amends`: amendments written before the launch, appended in order; `beside`: the
    (id, gist) of each item it is warned about starting beside (`BESIDE`)."""
    run = run or {}
    prose = cfg.opts(item.repo).prose
    keeps = audits.writes_notes(item.meta())
    text = PREAMBLE.format(
        item_id=item.id, project=cfg.project, workdir=_workdir(cfg, item, run),
        readonly=READONLY if item.readonly else "",
        paths=PATHS.format(globs=_ticks(item.paths), prose=PROSE.format(globs=_ticks(prose)) if prose else "")
        if item.paths else "",
        port=PORT.format(port=run["port"]) if run.get("port") else "",
        beside=BESIDE.format(items="\n".join(f"- {i}: {g}" for i, g in beside)) if beside else "",
        audit="\n" + audits.fill(audits.template_text("audit"), notes_path=str(paths.audit.resolve()))
        if keeps else "",
        only=ONLY_TWO if keeps else ONLY_ONE,
        calls=calls_text(cfg, item.repo),
        max_hours=max_hours, ask_minutes=cfg.ask_default_minutes,
        deadline=clock(deadline), report=paths.report.resolve(), pick=PICK,
        tail="" if resume else PROMPT_FOLLOWS,
    )
    if not resume:
        return text + body(cfg, item, notes, usage) + "".join(_amend(AMEND, m) for m in amends)
    n = resume["landed"]
    landed = (f"; {n} commit(s) landed since you staged (`git log --oneline {resume['old'][:10]}..HEAD`)"
              if n else "; nothing landed since you staged")
    head = RESUME_HEAD.format(old=resume["old"][:10], new=resume["new"][:10], main=cfg.opts(item.repo).main,
                              landed=landed)
    tail = (RESUME_AMENDS + "".join(_amend(RESUME_AMEND, m) for m in amends)) if amends else ""
    return head + text.rstrip() + "\nThe prompt is the one you read while staging.\n" + tail


def _amend(template: str, msg: dict) -> str:
    by = f", via {msg['by']}" if msg.get("by") and msg["by"] != "unknown" else ""
    return template.format(id=msg.get("id"), when=clock(msg["sent"]) if msg.get("sent") else "?", by=by,
                           text=str(msg.get("text", "")).strip())


def calls_text(cfg: Config, repo: str) -> str:
    """The preamble's *Short calls* paragraph, or nothing when the cap is off."""
    cap, bg = cfg.foreground_seconds, cfg.opts(repo).background
    if cap <= 0:
        return ""
    return CALLS.format(cap=cap, cap_ms=round(cap * 1000), bg=CALLS_BG.format(globs=_ticks(bg)) if bg else "")


def build_stage_prompt(cfg: Config, item: Item, run: dict) -> str:
    return STAGE.format(item_id=item.id, project=cfg.project, workdir=_workdir(cfg, item, run),
                        stage_minutes=cfg.stage_minutes, main=cfg.opts(item.repo).main) + body(cfg, item)


def body(cfg: Config, item: Item, notes: str = "", usage: str = "") -> str:
    """What follows the preamble: the item body, or for a `template:` item the
    template's text (a cleanup's with its audit notes inlined, a disk audit's with
    conductor's measurements) and then whatever the planner wrote."""
    if not item.template:
        return item.body
    text = audits.fill(audits.template_text(item.template), repo=item.repo, project=cfg.project,
                       notes=notes.strip(), usage=usage.strip()).rstrip() + "\n"
    if item.body.strip():
        text += f"\n---\n\nThe planner adds, for this item:\n\n{item.body}"
    return text


def mcp_config(cfg: Config, item: Item, session_id: str) -> str:
    """The executor's --mcp-config: the ask server, run by the daemon's own interpreter."""
    from .ask_server import SERVER_NAME
    return json.dumps({"mcpServers": {SERVER_NAME: {
        "type": "stdio", "command": sys.executable, "timeout": ASK_TIMEOUT_MS,
        "args": ["-m", "conductor.ask_server", "--control", str(cfg.control), "--item", item.id,
                 "--default-minutes", f"{cfg.ask_default_minutes:g}", "--session-id", session_id],
    }}}, indent=1)


def hook_settings(cfg: Config, item: Item) -> str:
    """The executor's --settings: the PostToolUse hook that delivers messages and the
    PreToolUse hook that keeps shell calls short (`foreground_seconds`, the repo's
    `background` globs), run by the daemon's console interpreter, and
    `"ultracode": true` for an `ultracode` item. The
    settings key is what turns ultracode on in `-p`: the keyword in the prompt does not,
    and neither does a message (hook context) or a later edit of this file."""
    from .procs import console_python
    from .talk import hook_settings as settings
    data = json.loads(settings(console_python(), cfg.control, item.id, cfg.foreground_seconds,
                               cfg.opts(item.repo).background))
    if item.ultracode:
        data["ultracode"] = True
    return json.dumps(data, indent=1)


def command(cfg: Config, session_id: str, mcp: Path | None, resume: bool = False,
            settings: Path | None = None) -> list[str]:
    """`--session-id` for a new session; `--resume` continues a staged one. A stage
    gets no --mcp-config (it may not ask) and no --settings (nobody messages a stage)."""
    from .procs import resolve
    return [resolve(cfg.command[0]), *cfg.command[1:], "-p", "--dangerously-skip-permissions",
            "--output-format", "stream-json", "--verbose", "--resume" if resume else "--session-id", session_id,
            *(["--mcp-config", str(mcp)] if mcp else []), *(["--settings", str(settings)] if settings else []),
            *cfg.args]


def read_result(log: Path) -> dict | None:
    """The final `{"type": "result", ...}` line of a stream-json log, or None."""
    if not log.is_file():
        return None
    result = None
    with open(log, "rb") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw.startswith(b"{"):
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if isinstance(obj, dict) and obj.get("type") == "result":
                result = obj
    return result


COMPACT_MIN_BYTES = 1 << 20     # a log smaller than this is left as it is
DROPPED = "dropped_by_conductor"


def _compact(obj, top: bool = True) -> int:
    """Strip the bulk out of one stream-json line in place: base64 payloads (an image
    the executor read, twice over: the tool result and `tool_use_result.file`) and
    `tool_use_result.originalFile` (a whole file before each edit). Returns the
    characters dropped. Both are copies of files that stay in the repo."""
    dropped = 0
    if isinstance(obj, dict):
        if top and isinstance(tur := obj.get("tool_use_result"), dict) \
                and isinstance(tur.get("originalFile"), str) and len(tur["originalFile"]) > 1024:
            dropped += len(tur["originalFile"])
            tur["originalFile"], tur[DROPPED] = "", len(tur["originalFile"])
        for key in ("data", "base64"):
            value = obj.get(key)
            if isinstance(value, str) and len(value) > 1024 and (key == "base64" or obj.get("type") == "base64"):
                dropped += len(value)
                obj[key], obj[DROPPED] = "", len(value)
        for value in obj.values():
            dropped += _compact(value, False)
    elif isinstance(obj, list):
        for value in obj:
            dropped += _compact(value, False)
    return dropped


def compact_log(log: Path) -> tuple[int, int] | None:
    """Shrink a finished item's stream-json log in place (see `_compact`); everything
    conductor and the app read from it (the result line, what was said, each tool call)
    stays. Returns (bytes before, bytes after), or None when it was left alone."""
    try:
        before = log.stat().st_size
    except OSError:
        return None
    if before < COMPACT_MIN_BYTES:
        return None
    out, dropped = [], 0
    with open(log, "rb") as fh:
        for raw in fh:
            try:
                obj = json.loads(raw)
            except ValueError:
                out.append(raw)
                continue
            n = _compact(obj)
            dropped += n
            out.append(json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n" if n else raw)
    if not dropped:
        return None
    tmp = log.with_name(log.name + ".compact")
    tmp.write_bytes(b"".join(out))
    os.replace(tmp, log)
    return before, log.stat().st_size


def read_header(report: Path) -> tuple[dict | None, str | None]:
    """(header, problem). Accepts the three lines bare or inside a `---` block, then
    `slow:` (what took long; absent is no problem) and an optional `pick:` (a picker
    manifest's path). Those two only right after the three, never in the body."""
    if not report.is_file():
        return None, "no report written"
    lines = [ln.strip() for ln in read_text(report).splitlines()]
    lines = [ln for ln in lines if ln and ln != "---"][:5]
    header = {}
    for n, ln in enumerate(lines):
        m = re.match(r"^\**(status|blocks|carry|slow|pick)\**\s*:\s*(.*)$", ln, re.IGNORECASE)
        key = m.group(1).lower() if m else None
        if key is None or key in header:
            continue                    # the first of each key counts; a repeat is body
        if key in HEADER_KEYS:
            if n < 3:
                header[key] = m.group(2).strip()
            continue
        # `slow:` and `pick:` only as the lines straight after the three (either order):
        # a fifth line is theirs only when the fourth was the other one.
        if not all(k in header for k in HEADER_KEYS) or n - 3 != sum(k in header for k in ("slow", "pick")):
            continue
        value = m.group(2).strip()
        if key == "slow":
            if value:
                header["slow"] = value
            continue
        # `pick:` as one path: a line that merely starts "Pick: went with the warmer
        # palette" is the body's.
        value = value.strip("`").strip()
        if value and len(value.split()) == 1:
            header["pick"] = value
    missing = [k for k in HEADER_KEYS if k not in header]
    if missing:
        return header or None, f"report header missing {', '.join(missing)}"
    status = header["status"].lower().split()[0] if header["status"] else ""
    if status not in STATUSES:
        return header, f"report status {header['status']!r} is not done/partial/red"
    header["status"] = status
    return header, None
