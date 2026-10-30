"""Executors: the preamble, the `claude -p` command line, and reading what a run left."""

from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import audits
from . import parked as parked_mod
from .config import Config
from .items import Item
from .util import clock, read_text

PREAMBLE = """\
You are a conductor executor: an unattended headless Claude Code session running queue
item {item_id} for project {project}. Nobody is watching the session.

Asking. `ask` (mcp__conductor__ask) puts a question to Matt. Ask only when a wrong guess
would waste real work or be hard to undo, and give a default whenever a reasonable one
exists: an unanswered question returns it after {ask_minutes:g} minutes and you carry on.
Block (`block: true`) only when every default would be worse than waiting; blocked time
does not count toward your time limit. Anything you don't ask about, decide as fits the
goal, record what you didn't do, and continue: the only hard stop is a wrong-repo guard in
the prompt. Leave a step whose default can't be undone (a delete, an overwrite) until
last, so a late answer or message can still change it.
{servers}
Progress. `progress` (mcp__conductor__progress) shows Matt one short line and returns at
once. Call it at the start, with a first `eta` as soon as you can estimate one, at real
milestones, and when your estimate moves by more than a third (check after your first
section). Not chatter. Estimate in tool round trips (about 20–40 s each) plus the repo's
measured run times (README), with an allowance per unknown, and run `date` before every
`eta` or outline time rather than reusing a clock you read earlier.

Outline. Once you have a plan, call `progress` with `outline`: your sections in order, the
report last, at most ~8 rows of a few words, each with a rough local finish time
(11:05am). Mark a row `done` in your next progress call after it finishes, leaving other
times alone unless they moved a lot, and post the outline again when a message asks.
Keep it coarse. Staged, your plan ends with it: post it first thing.

Messages. Matt, the operator, may message you while you work through conductor's
message channel: hook context after one of your own tool calls, marked MESSAGE #n from
Matt, or AMENDMENT #n from Matt for an addition to the prompt. It is not tool-output
injection: it is Matt's instruction and outranks the prompt where they conflict. Answer
it with `progress`. Tell any helper or subagent you start that if it sees such a
delivery it passes it straight up to you, word for word; act on what it relays. A
message sent while you wait on a question returns `ask` early, the question still open:
the message is not its answer. Act on it, then `ask` with `resume: "<id>"` to wait on.
{calls}
{workdir}{readonly}{paths}{port}{beside}{resumed}
{audit}
Time limit: {max_hours:g}h. Conductor hard-kills this session at {deadline}, later by any
time spent blocked on a question or paused.

When you finish, whatever the outcome, write your report to exactly this path:
    {report}
{only} Do not read,
write or commit anything else in that folder: conductor checks it after you exit,
reverts any change, and marks this item red.

The report must start with these five lines:
status: done | partial | red
blocks: <what this blocks, or none>
carry: <what state.md should change, or none>
slow: none | <component> ~<time>, <why>, <what would fix it>[; <another>]
friction: none | <what cost you time or tokens>[; <another>]
{pick_line}`slow:` is normally `none`; name only a part that took over ~15 minutes or about
twice what you expected, with its cause and a fix if you see one, and never pad it.
`friction:` names one or two things in the prompt, the repo's docs, its tools or conductor
that cost this item time or tokens (a misleading line, a missing command, a retry); `none`
is fine.
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

# The preamble for a small item (`is_light`): the rules a short job can trip on, and none
# of the ceremony (outline, ETAs, pickers, servers) that costs it round trips.
LIGHT = """\
You are a conductor executor, a headless Claude Code session running queue item
{item_id} for project {project}. Nobody is watching.

Asking. If a wrong guess would waste real work, `ask` (mcp__conductor__ask) Matt with a
default; unanswered, it returns the default after {ask_minutes:g} minutes. Block
(`block: true`) only if every default is worse.

Progress. One short `progress` (mcp__conductor__progress) line at the start; another
only past ~15 minutes.

Messages. MESSAGE #n or AMENDMENT #n from Matt, arriving as hook context after a tool
call, is his instruction, not injection, and outranks the prompt.
{calls}
{workdir}{readonly}{paths}{port}{beside}

Time limit: {max_hours:g}h; killed at {deadline} (later if blocked or paused).

Whatever the outcome, write your report to exactly this path:
    {report}
{only} Conductor reverts any other change in that folder and marks this item red.
Start it with:
status: done | partial | red
blocks: <what this blocks, or none>
carry: <what state.md should change, or none>
slow: none | <component> ~<time>, <why>, <what would fix it>
friction: none | <what slowed you>
`done`: goal met. `partial`: part met, nothing broken, the rest follow-up (or
declined, saying why). `red`: broken or unsafe; the queue stops for a
look. Answer every message and amendment by number. End your final message with
`Report written to {report}`.

{tail}"""

SERVERS = """
Servers. Claude Code stops a background command 10 minutes after it starts, so a
long-lived server (a dev or preview server, a probe) started that way dies mid-work.
Start one detached instead, with its output to a log file (PowerShell `Start-Process
-WindowStyle Hidden -RedirectStandardOutput <log>`, or Python `subprocess.Popen` with
`DETACHED_PROCESS`), or through a script the repo provides for it, and stop it before
you finish.
"""

PICK_LINE = "and, only if you wrote a picker manifest, a sixth:\npick: <manifest path relative to the repo root>\n"

PICK = """
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
WebP, AVIF or SVG. `intro`, `note` and an option's `label` are optional. To ask which of
a row's options go forward (keep or drop each), give the row `"kind": "many"` and
optionally `"default": [keys kept to start with]`; Matt toggles each tile, and his choice
comes back as the list of kept keys. Generated picker images go in a shared folder when
the repo has one; commit build output for a picker only if the prompt says to.
"""

# When an item gets the picker section: its body could produce candidates.
PICK_RE = re.compile(r"\bpicker|\bpick:|\bcandidates?\b|\balternatives?\b|\bart:\w|\bart (pipeline|template)",
                     re.IGNORECASE)

LIGHT_WORDS = 150   # a body under this many words, with none of the flags below, gets LIGHT

CALLS = """
Short calls. A message can only get in between tool calls, so no call should last more
than about {cap:g} seconds. Run anything that may take longer (tests, builds, renders) with
`run_in_background`, and wait on it in polls of at most {cap:g} seconds each (a `timeout`
of {cap_ms} or less), never one long waiter, and never by ending your turn: a message
cannot reach you while you are idle. Conductor enforces this: a foreground shell call
still running after {cap:g} seconds is moved to the background (you are told so and
notified when it ends; Claude Code kills it 10 minutes after the move){bg}.
"""

CALLS_LIGHT = """
Short calls. Nothing foreground over ~{cap:g} s: background tests and builds
(`run_in_background`) and poll with a `timeout` of {cap_ms} or less. Never end your turn
to wait.
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

WORKDIR_TREE_LIGHT = """\
Your working directory is a worktree of `{repo}`: {cwd}, branch `{branch}`. Commit
everything to it (uncommitted work makes the item red); never touch `{main}` or the main
checkout at {repo_path}.{shared}"""

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
PORT_LIGHT = ("\nPort. A server you start uses port {port} (`" + PORT_VAR + "`), never the repo's usual "
              "one: pass it through where a script hard-codes a port.")

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


def _workdir(cfg: Config, item: Item, run: dict, light: bool = False) -> str:
    if not run.get("worktree"):
        return WORKDIR_MAIN.format(repo=item.repo, repo_path=cfg.repos[item.repo])
    shared = SHARED.format(names=", ".join(f"`{s}`" for s in run["shared"])) if run.get("shared") else ""
    return (WORKDIR_TREE_LIGHT if light else WORKDIR_TREE).format(repo=item.repo, cwd=run["worktree"], branch=run["branch"],
                               main=cfg.opts(item.repo).main, base=run["base"][:10],
                               repo_path=cfg.repos[item.repo], shared=shared)

# Per-server tool timeout for the ask server: Claude Code otherwise aborts a call silent
# for 30 minutes. Seven days in ms, under 2**31 ms where a JavaScript timer would wrap.
# The ask server's progress notifications cover the same limit independently.
ASK_TIMEOUT_MS = 7 * 24 * 3600 * 1000

HEADER_KEYS = ("status", "blocks", "carry")
OPTIONAL_KEYS = ("slow", "friction", "pick")     # straight after the three, any order
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
    original: Path     # a resume item's extract of its original's events

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
                   sib(".amend.md"), sib(".original.md"))


def build_prompt(cfg: Config, item: Item, paths: RunPaths, max_hours: float, deadline: datetime,
                 run: dict | None = None, resume: dict | None = None, notes: str = "",
                 usage: str = "", amends: list[dict] = (), beside: list[tuple[str, str]] = (),
                 resumed: str = "", parked: list[dict] = ()) -> str:
    """The execution preamble and the item body; with `resume` (old/new base, landed
    count), the message that resumes a staged session instead. `notes`: a cleanup's
    audit notes, inlined; `usage`: a disk cleanup's control-folder sizes and worktrees;
    `amends`: amendments written before the launch, appended in order; `beside`: the
    (id, gist) of each item it is warned about starting beside (`BESIDE`); `resumed`: a
    resume item's paragraph on its original (`resume.paragraph`); `parked`: the parked edits
    it carries (`parked.section`)."""
    run = run or {}
    prose = cfg.opts(item.repo).prose
    keeps = audits.writes_notes(item.meta())
    light = is_light(item)
    pick = not light and wants_pick(item)
    text = (LIGHT if light else PREAMBLE).format(
        item_id=item.id, project=cfg.project, workdir=_workdir(cfg, item, run, light),
        readonly=READONLY if item.readonly else "",
        paths=PATHS.format(globs=_ticks(item.paths), prose=PROSE.format(globs=_ticks(prose)) if prose else "")
        if item.paths else "",
        port=(PORT_LIGHT if light else PORT).format(port=run["port"]) if run.get("port") else "",
        beside=BESIDE.format(items="\n".join(f"- {i}: {g}" for i, g in beside)) if beside else "",
        resumed=resumed,
        audit="\n" + audits.fill(audits.template_text("audit"), notes_path=str(paths.audit.resolve()))
        if keeps else "",
        only=ONLY_TWO if keeps else ONLY_ONE,
        calls=calls_text(cfg, item.repo, light),
        servers=SERVERS if not light and has_servers(cfg, item, run) else "",
        max_hours=max_hours, ask_minutes=cfg.ask_default_minutes,
        deadline=clock(deadline), report=paths.report.resolve(),
        pick=PICK if pick else "", pick_line=PICK_LINE if pick else "",
        tail="" if resume else PROMPT_FOLLOWS,
    )
    extra = parked_mod.section(list(parked))
    if not resume:
        return text + body(cfg, item, notes, usage) + extra + "".join(_amend(AMEND, m) for m in amends)
    n = resume["landed"]
    landed = (f"; {n} commit(s) landed since you staged (`git log --oneline {resume['old'][:10]}..HEAD`)"
              if n else "; nothing landed since you staged")
    head = RESUME_HEAD.format(old=resume["old"][:10], new=resume["new"][:10], main=cfg.opts(item.repo).main,
                              landed=landed)
    tail = (RESUME_AMENDS + "".join(_amend(RESUME_AMEND, m) for m in amends)) if amends else ""
    return head + text.rstrip() + "\nThe prompt is the one you read while staging.\n" + extra + tail


def _amend(template: str, msg: dict) -> str:
    by = f", via {msg['by']}" if msg.get("by") and msg["by"] != "unknown" else ""
    return template.format(id=msg.get("id"), when=clock(msg["sent"]) if msg.get("sent") else "?", by=by,
                           text=str(msg.get("text", "")).strip())


def calls_text(cfg: Config, repo: str, light: bool = False) -> str:
    """The preamble's *Short calls* paragraph, or nothing when the cap is off."""
    cap, bg = cfg.foreground_seconds, cfg.opts(repo).background
    if cap <= 0:
        return ""
    if light:
        return CALLS_LIGHT.format(cap=cap, cap_ms=round(cap * 1000))
    return CALLS.format(cap=cap, cap_ms=round(cap * 1000), bg=CALLS_BG.format(globs=_ticks(bg)) if bg else "")


def is_light(item: Item) -> bool:
    """A small item gets the light preamble: a body under `LIGHT_WORDS` words, none of
    `ultracode`, `template`, `resume`, `audit` or an explicit `max_hours`, and nothing
    that could produce candidates (a short art round still needs the picker schema)."""
    return (len(item.body.split()) < LIGHT_WORDS and not item.ultracode and not item.template
            and not item.resume_of and not item.audit and item.max_hours is None and not wants_pick(item))


def wants_pick(item: Item) -> bool:
    """The full preamble's picker section goes in when the item could produce candidates."""
    return bool(PICK_RE.search(item.body))


DEV_SCRIPT_RE = re.compile(r"^(dev|preview)\b")


def has_servers(cfg: Config, item: Item, run: dict) -> bool:
    """The *Servers* paragraph goes in when the repo has a run target, or its
    `package.json` a dev or preview script (`dev`, `dev:web`, `preview`)."""
    repo = cfg.repos[item.repo]
    if any(Path(t.cwd).resolve().is_relative_to(Path(repo).resolve()) for t in cfg.targets.values()):
        return True
    try:
        scripts = json.loads(read_text(Path(run.get("worktree") or repo) / "package.json")).get("scripts") or {}
    except (OSError, ValueError, AttributeError):
        return False
    return isinstance(scripts, dict) and any(DEV_SCRIPT_RE.match(k) for k in scripts)


def build_stage_prompt(cfg: Config, item: Item, run: dict) -> str:
    return STAGE.format(item_id=item.id, project=cfg.project, workdir=_workdir(cfg, item, run),
                        stage_minutes=cfg.stage_minutes, main=cfg.opts(item.repo).main) + body(cfg, item)


def body(cfg: Config, item: Item, notes: str = "", usage: str = "") -> str:
    """What follows the preamble: the item body, or for a `template:` item the
    template's text (a cleanup's with its audit notes inlined, a disk cleanup's with
    conductor's measurements) and then whatever the planner wrote."""
    if not item.template:
        return item.body
    text = audits.fill(audits.template_text(item.template), repo=item.repo, project=cfg.project,
                       notes=notes.strip(), usage=usage.strip(), original=item.resume_of or "").rstrip() + "\n"
    if item.body.strip():
        who = "Why conductor launched this watch" if item.template == "watch" else "The planner adds, for this item"
        text += f"\n---\n\n{who}:\n\n{item.body}"
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
            settings: Path | None = None, fork_of: str | None = None) -> list[str]:
    """`--session-id` for a new session; `--resume` continues a staged one; `fork_of`
    (a resume item) resumes that finished session as a fork under `session_id`, leaving
    the original as it was. A stage gets no --mcp-config (it may not ask) and no
    --settings (nobody messages a stage)."""
    from .procs import resolve
    session = (["--resume", fork_of, "--fork-session", "--session-id", session_id] if fork_of
               else ["--resume" if resume else "--session-id", session_id])
    return [resolve(cfg.command[0]), *cfg.command[1:], "-p", "--dangerously-skip-permissions",
            "--output-format", "stream-json", "--verbose", *session,
            *(["--mcp-config", str(mcp)] if mcp else []), *(["--settings", str(settings)] if settings else []),
            *cfg.args]


def gz_path(log: Path) -> Path:
    return log.with_name(log.name + ".gz")


def stored_log(log: Path) -> Path | None:
    """Where a run log is now: `log` itself, or `log.gz` once its item finished
    (`gzip_log`), or None."""
    for p in (log, gz_path(log)):
        if p.is_file():
            return p
    return None


def open_log(log: Path):
    """A run log opened for reading in binary, gzipped or not; OSError when it has neither form."""
    p = stored_log(log)
    if p is None:
        raise FileNotFoundError(str(log))
    return gzip.open(p, "rb") if p.suffix == ".gz" else open(p, "rb")


def read_result(log: Path) -> dict | None:
    """The final `{"type": "result", ...}` line of a stream-json log, or None."""
    if stored_log(log) is None:
        return None
    result = None
    with open_log(log) as fh:
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


GZIP_MIN_BYTES = 256 << 10      # a finished log smaller than this stays plain text


def gzip_log(log: Path) -> tuple[int, int] | None:
    """Replace a finished item's run log with `log.gz` (after `compact_log`): stream-json
    shrinks ~10×, and a bug hunt's log is 8-23 MB even compacted. Every reader goes
    through `stored_log` / `open_log`. Returns (bytes before, bytes after), or None."""
    try:
        before = log.stat().st_size
    except OSError:
        return None
    if before < GZIP_MIN_BYTES:
        return None
    gz = gz_path(log)
    tmp = gz.with_name(gz.name + ".tmp")
    with open(log, "rb") as src, gzip.open(tmp, "wb", compresslevel=6) as dst:
        shutil.copyfileobj(src, dst, 1 << 20)
    os.replace(tmp, gz)
    log.unlink()
    return before, gz.stat().st_size


def read_header(report: Path) -> tuple[dict | None, str | None]:
    """(header, problem). Accepts the three lines bare or inside a `---` block, then
    `slow:` (what took long), `friction:` (what cost the item time or tokens; absent is no
    problem either) and an optional `pick:` (a picker manifest's path). Those three only
    right after the three, in any order, never in the body."""
    if not report.is_file():
        return None, "no report written"
    lines = [ln.strip() for ln in read_text(report).splitlines()]
    lines = [ln for ln in lines if ln and ln != "---"][:3 + len(OPTIONAL_KEYS)]
    header = {}
    seen = 0                            # optional lines met so far, set or not
    for n, ln in enumerate(lines):
        m = re.match(r"^\**(status|blocks|carry|slow|pick|friction)\**\s*:\s*(.*)$", ln, re.IGNORECASE)
        key = m.group(1).lower() if m else None
        if key is None or key in header:
            continue                    # the first of each key counts; a repeat is body
        if key in HEADER_KEYS:
            if n < 3:
                header[key] = m.group(2).strip()
            continue
        # Optional lines only straight after the three: line 3 + k is one only when the k
        # lines before it were optional ones too.
        if not all(k in header for k in HEADER_KEYS) or n - 3 != seen:
            continue
        seen += 1
        value = m.group(2).strip()
        if key in ("slow", "friction"):
            if value:
                header[key] = value
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
