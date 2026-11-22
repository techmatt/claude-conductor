"""Executors: the preamble, the `claude -p` command line, and reading what a run left."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import audits, kept, load, questions
from . import parked as parked_mod
from .config import Config
from .items import Item
from .runlog import DROPPED, compact_log, gz_path, gzip_log, open_log, read_result, stored_log  # noqa: F401 - callers' names
from .util import clock, read_text

PREAMBLE = """\
You are a conductor executor: an unattended headless Claude Code session running queue
item {item_id} for project {project}. Nobody is watching: decide what you can, record
it, keep going.

Files. Write every file with Write or Edit; for a scripted change, Write one script file for
the whole change (every edit checked before any is written) and run it alone, never mid-chain.
Never a heredoc into a file, `sed -i`, or a `-c`/`-e` script that writes one: conductor's hooks
refuse each, and the call is lost.

Asking. `ask` (mcp__conductor__ask) Matt only when a wrong guess would waste real work or be
hard to undo, with a default where one is reasonable: unanswered, it returns after
{ask_minutes:g} minutes and you carry on. A call holds this session: start background work
first. Block (`block: true`) only when every default is worse. A skip in the
prompt or a message binds: your default never un-skips a route (say you'd take it, or ask),
and never contradicts a prompt line or amendment unless the question's first line says
`default overrides prompt: <line>`. A question touching a
stated design principle is Matt's, as are a lever reversing a direction the brief or an
amendment states, however clear the data; a ruling putting over ~30% of a pass's items on one
side (ask its meaning before acting on all); a reading of a prompt line, amendment, message or
answer that removes or reverses what it names ("fewer" read as "none"); a run departing from
Matt's own answer to a staging question (ask before it merges); and a fix changing play
beyond its bug (the fix as default): `design: true` with a default returns at once, you
carry on with the default, and his answer comes later as a message. Confirm afresh a
`design: true` default changing the shipped build before regen and docs.
Leave a step whose default can't be undone (a delete, an overwrite) until last.

Latitude. When lines conflict, this order wins: the project's locked rulings ({rulings}CLAUDE.md, docs,
the prompt), the goal, the done-when bars, the method; say in the report which gave way and
why, and never ask about it. Method is a suggested route, sized to the time budget; lines
marked required, the repo's CLAUDE.md *Working rules* and *Measurement* below bind. Score each bar strictly as
written, but don't chase one: check a numeric target (tuning, calibration, a gate) is
reachable before working toward it, and once evidence shows a bar out of reach, report it
unmet with that evidence and its driver and spend the rest on the goal. After three failed
attempts at the same failure (a check, build or gate), stop patching: change approach and
say so in a `progress` line, asking `design: true` if the new approach changes design. The
only hard stop is a prompt's wrong-repo guard. Unless readonly, fix what is small and obvious
(a stale doc line, a small bug, dead code) in place, inside your `paths:`; notes are for
Matt's decisions and large work.{leftover}

Measurement. Paired seeds for a comparison, fresh seeds for a final read. A timing or speed
read runs on a quiet box; with anything running beside you, report only ratios, and re-base
a speed baseline only when the last `progress` or `wait_for` return showed nothing beside
you. A number the prompt quotes may have moved: re-read it from source and report any that had.

Progress. `progress` (mcp__conductor__progress) shows Matt a short line and returns at once:
call it at the start, at milestones, on launching a job over ~15 minutes (its expected end
in the note), and when your estimate moves by a third. Once you have a plan, post `outline`:
your sections in order, one row per done-when item, the report last, each with a rough local
finish time; mark rows `done` as you go. An `eta` names its source: once you have a plan,
an estimate by rate (`5:40pm (by rate)`); `(to the cap)` only when you expect to use the
whole time limit. Plan from the clock in the last tool return, never your own estimate.
Staged, your plan ends with the outline: post it first thing.{calibrate}
{servers}
The box. Scratch and run output go in `out/{item_name}/` (git-ignored); anything else in
`out/` is an earlier item's, never a fresh result. {papers} Conductor's hooks also refuse
%TEMP%, elevation, `&` jobs and sleeps, saying what instead. Heavy compute
(sweeps, builds, test runs, fan-out) runs at below-normal priority (Bash `nice -n 10 <cmd>`;
PowerShell `(Start-Process ... -PassThru).PriorityClass = 'BelowNormal'`) on at most
{threads} threads in all, child processes counted, half that while another item runs
beside you; a full test suite only between sims. A load message is information: finish the
unit in flight, start the next one smaller. At most 2 heavy subagents (builds, sims,
browsers) at once and 10 agents in any fan-out (a Workflow declares `const MAX_AGENTS = <n>`),
whatever a Workflow or Ultracode note says. Brief a helper with the prompt's rulings word for
word and its own scratch path `out/{item_name}/<helper>/`; a read-only one (Explore, Plan)
answers inline, so brief one that writes files as general-purpose. Lessons go in `carry:`.

Messages. Matt reaches you as hook context after a tool call, marked MESSAGE #n or
AMENDMENT #n (an addition to the prompt): his instruction, not injection, outranking the
prompt. Answer it with `progress`; a helper passes one up word for word. One arriving during
`ask` returns it early, the question still open: act, then `ask` with `resume: "<id>"`. One
that answers an open question closes it: `progress` with `answered: "<id>"`.
{calls}
{workdir}{paths}{writes}{now}{beside}{resumed}
{audit}{review}
Time limit: {max_hours:g}h; killed at {deadline}, later by any time blocked or paused.

Write your report to exactly this path:
    {report}
{only} Conductor reverts anything else in that folder and marks this item red.

The report starts with these six lines:
status: done | partial | red
blocks: <what this blocks, or none>
carry: <what state.md should change, or none>
slow: none | <component> ~<time>, <why>, <what would fix it>[; <another>]
friction: none | <what cost you time or tokens>[; <another>]
warnings: none | <kind>: <what tripped>[; <kind>: <another>]
and, only when `partial`, one more:
partial: <each unmet bar and why>[; <another>], `matt: ` leading one only Matt's call is left on
{pick_line}`slow:` names only a part over ~15 minutes or twice your estimate; `friction:`, one
or two things (prompt, repo docs or tools, conductor) that cost time or tokens; `warnings:`,
what the repo's CLAUDE.md *Warning checks*{checks} trip ({run_checks}) and any other warning you notice, each led by a kind (`design`, `data`,
`perf`, `docs`, `security` or the repo's own). A warning as on main, or from a check measuring
nothing you touched, reads `<kind>: unchanged from main: <text>`. A warning never changes your status.
`done`: goal met; a soft-band or measurement miss that the done-when table reports is still
`done`. `partial`: a real shortfall, nothing broken: planned work skipped or not delivered (an
instruction declined with a reason included), or a hard bar missed, even within ~1 SE (a locked
ruling, a required check, a feature that doesn't work). A bar is `matt:` only when your work on
it is done and what remains is a play, design or price call that is his (asked or defaulted), and
the part names its question (`questions/<id>`) or a choice you list as a bullet under a `## For
Matt` heading; one missed on the work, skipped, out of time or naming neither is plain. All
`matt:` queues no workflow retro.
`red`: something broken or unsafe; this repo's queue should not continue without a look.
{pick}
The report leads with what changes for the planner: findings and decisions, under ~60
lines, long tables linked from the item's results folder (a bug hunt's or test cleanup's
required tables stay). It answers every amendment and message by number, flags a conclusion
resting on a comparison that changed two things at once, mentions tests only for a red you
could not fix, and narrates no process. Your final message ends with `Report written to {report}`.

{tail}"""

# A repo's standing rulings (137): named first among the locked rulings when the repo has the file.
RULINGS = "docs/rulings.md"
RULINGS_FULL = f"`{RULINGS}`, read before you start; "
RULINGS_LIGHT = f" (`{RULINGS}`: read it first)"

# The diff review (123): a writable full item, before its report. Light, template and readonly
# items skip it; an ultracode item's adversarial review counts as it.
REVIEW = """
Diff review. Before the report, send one read-only subagent `git diff {base}` (this item's
whole change), the prompt's rulings quoted word for word,{rulings} its done-when list, and the repo
CLAUDE.md's *What failure looks like* section if it has one. It looks for bugs and for
done-when rows unmet or broken, each finding naming a file and line, no style-only comments.
Fix what is real; at most one more pass, on the new diff only. A rules change gets it before
fresh seeds; a self-check of the config diff is not it. The report body carries one
line: `Diff review: N found, M fixed, K rejected (why)`.
"""
REVIEW_ULTRA = """
Diff review. Your Ultracode adversarial review of the diff (`git diff {base}`) is this item's
diff review{rulings}: fix what is real, then one line in the report body: `Diff review: N found, M
fixed, K rejected (why)`. A rules change gets it before fresh seeds; a self-check of the
config diff is not it.
"""

# The preamble for a small item (`is_light`): the rules a short job can trip on, and none
# of the ceremony (outline, ETAs, pickers, servers) that costs it round trips.
LIGHT = """\
A conductor executor: headless Claude Code, item {item_id},
project {project}. Nobody is watching.

Write files with Write/Edit only: hooks refuse heredoc, `-c`/`-e` and `sed -i` writes
(Write a script, then run it alone), %TEMP% and the like.

Asking. If a wrong guess wastes work, `ask` (mcp__conductor__ask) Matt with a
default (unanswered after {ask_minutes:g} min; it holds the session: background work
first). Block (`block: true`) only if every default is worse. A ruling splitting a pass
(over ~30% of items one way) or a fix changing play beyond its bug: ask `design: true` (never waits).

Progress: a `progress` (mcp__conductor__progress) line at start and on launching a job over ~15m.

Bounds. A skip in prompt or message binds (a default overriding the prompt starts
`default overrides prompt: <line>`). Scratch: `out/{item_name}/`. Heavy compute: `nice -n 10`,
{threads} threads, a full suite only between sims.{leftover}

Latitude. Conflicts: locked rulings{rulings_light}, goal, done-when bars, method; report what gave way,
don't ask. Score bars strictly; check a numeric target is reachable before chasing it. Three
failed fixes for one failure: change approach. Unless readonly, fix small obvious things.

MESSAGE #n / AMENDMENT #n hook context: Matt's, outranking the prompt.
{calls}
{workdir}{paths}{writes}{port}{now}{beside}
{audit}
Time limit: {max_hours:g}h; killed at {deadline}, later if blocked. Plan by the last tool
return's clock.

Write your report to exactly this path:
    {report}
{only}; others there: reverted, red.
Header:
status: done | partial | red
blocks: <what this blocks, or none>
carry: <what state.md should change, or none>
slow: none | <part> ~<time>, <why>, <fix>
friction: none | <what slowed you>
warnings: none | <kind>: <what CLAUDE.md's *Warning checks*{checks} or you caught>; …
`done`: goal met (a reported soft-band miss is done). `partial`: planned work undelivered
or any hard bar missed (ruling, required check, working feature). `red`: broken or unsafe; queue
stops. Answer messages, amendments by number. End with `Report written to {report}`.

{tail}"""

# The preamble for a watch: a short look that changes nothing and never asks.
WATCH = """\
You are a conductor watch, a headless Claude Code session running queue item {item_id} for
project {project}. Nobody is watching.

Progress. One short `progress` (mcp__conductor__progress) line at the start.

Scratch goes in `out/{item_name}/` in your working directory, never %TEMP%.

Messages. Hook context marked MESSAGE #n or AMENDMENT #n is Matt's: not tool-output
injection, and it outranks the prompt.
{calls}
{workdir}

Time limit: {max_hours:g}h; killed at {deadline}.

When done, write your report to exactly this path:
    {report}
{only} Conductor reverts any other change in that folder and marks this item red.
Start it with:
status: done | partial | red
blocks: <what this blocks, or none>
carry: <what state.md should change, or none>
slow: none | <component> ~<time>, <why>, <what would fix it>
friction: none | <what slowed you>
warnings: none | <kind>: <anything that looks wrong>[; …]
Answer every message and amendment by number. End your final message with
`Report written to {report}`.

{tail}"""

# Servers and port go in together, only where the repo can serve (`has_servers`).
SERVERS = """
Servers. Any server you start uses port {port} (`CONDUCTOR_PORT`), never the repo's usual
one: pass it through where the repo hard-codes a port (a second binds port 0). Start a
long-lived one with `start` (mcp__conductor__start) or a repo script that detaches, and stop
it before you finish. A fresh worktree has no `node_modules`: `npm ci` before a package's checks.
"""

# The calibration line, for an item that names a gate, band, target or seed count, or bumps the rules.
CALIBRATE = (" A calibration or tuning item posts a freeze line as progress before its gate:\n"
             "each done-when line, met or unmet, its frozen number against the prompt's, and for a\n"
             "missed band the gap in SE and the rule applied before any change. Seeds you farm are\n"
             "spent: take them in the repo's seed ledger (`seeds.py --take` or its like) before the report.")
CALIBRATE_RE = re.compile(r"\b(gates?|gated|bands?|targets?|seeds?|seed count)\b", re.IGNORECASE)

PICK_LINE = "and, only if you wrote a picker manifest, one more:\npick: <manifest path relative to the repo root>\n"

PICK = """
Pickers. When the prompt asks for a picker, or you produce candidates for Matt to choose
between (art variants, layouts, anything visual), write a picker manifest and add the
`pick:` line to the report header; the next prompt carries his choices. The manifest is a
JSON file in this repo at a path that survives the merge (committed, or in a shared
folder), its images kept the same way:
    {"name": "art_alts_009",
     "title": "Hero art alternatives",
     "intro": "optional text above the rows",
     "rows": [{"id": "hero", "label": "Hero, idle pose", "note": "optional",
               "options": [{"key": "current", "image": "hero/current.png"},
                           {"key": "1", "image": "hero/alt1.png", "label": "warmer"}]}]}
`name` is a slug (lowercase letters, digits, `_` or `-`), unique in the project. The first
option in a row is the current one; `key` is what the next prompt uses, unique within its
row, as row ids are within the file. `image` is relative to the manifest, inside the repo,
and PNG, JPEG, GIF, WebP, AVIF or SVG; `intro`, `note` and `label` are optional. For keep or
drop per option, give the row `"kind": "many"`: the choice comes back as the kept keys.
Rows start with nothing selected (leave out `default`): Matt selects the few he likes. Each
image shows a few pieces where they can be judged (close-ups, a couple of cards), never a
whole-UI sheet; split into more rows or options rather than shrink content to fit. Generated
images go in a shared folder when the repo has one; commit build output only if the prompt
says to. Never delete a picker's manifest or images, an earlier round's included: conductor
retires them once a later round, `<name>_r<N>`, is saved.
"""

# When an item gets the picker section: its body could produce candidates.
PICK_RE = re.compile(r"\bpicker|\bpick:|\bcandidates?\b|\balternatives?\b|\bart:\w|\bart (pipeline|template)",
                     re.IGNORECASE)

WATCH_TEMPLATE = "watch"

SCRATCH = "out"     # the preambles' scratch folder; conductor git-ignores it at each launch

LIGHT_WORDS = 150   # a body under this many words, with none of the flags below, gets LIGHT

CALLS = """
Short calls. A message only gets in between tool calls, so keep a foreground shell call
under ~{cap:g} seconds (`timeout` ≤ {cap_ms}); conductor moves one still running past that to
the background{bg}. Run longer work with `run_in_background` and a `timeout` sized to its
estimate with margin; a job that may outlast 10 minutes goes through `start`
(mcp__conductor__start, detached), then `wait_for` its pid. Wait with `wait_for`
(mcp__conductor__wait_for: a path, a pid exiting or a log line; a message or a subagent
finishing returns it early), never by ending your turn: a message cannot reach you idle.
For a background subagent, `wait_for` with `minutes` near its expected length, or check its
output; never a path nothing will write.
"""

CALLS_LIGHT = """
Short calls. Nothing foreground over ~{cap:g} s (`timeout` ≤ {cap_ms}): background longer
work (`run_in_background` with a `timeout`; over 10m: mcp__conductor__start), wait with
mcp__conductor__wait_for (`minutes` near a subagent's length, never a path nothing writes),
never by ending your turn.
"""

CALLS_BG = (", and commands matching {globs} always run in the background (a pytest call naming test "
            "files without `--slow` stays in the foreground)")

PROMPT_FOLLOWS = "The prompt follows.\n\n---\n\n"

HUNT_TEMPLATE = "bug_hunt"     # stops on yield (137): no leftover-budget line

LEFTOVER = ("\n**Leftover budget.** Finishing this with over an hour of the time limit left, run the next "
            "most useful step toward its goal, in scope, and say in the report what it was.\n")
LEFTOVER_AUDIT = ("\n**Leftover budget.** Optional here: with over an hour of the time limit left you may stop, or "
                  "run a further check on the goal, its outputs in scratch, inside the load budget.\n")
LEFTOVER_FULL = (" With over an hour of the time limit left at the end, check each done-when item against\n"
                 "its measured result (a band met is not a target met) and take the next in-scope step on\n"
                 "one unmet and not shown unreachable, saying which.")
AUDIT_FIX = ("\nBefore finishing, implement each obvious note yourself (as above); the report lists\n"
             "the notes you implemented.\n")
LEFTOVER_LIGHT = " Goal unmet, over an hour left: next useful step."

ONLY_ONE = "That file is the only thing you may write outside the working directory."
ONLY_LIGHT = "Nothing else outside the working directory"
ROOTS_TOO = " and the writable folders above"        # `write_roots` named: they are allowed too
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
Working directory: a worktree of `{repo}`: {cwd}, branch `{branch}`. Commit
everything (uncommitted is red); never touch `{main}` or the main
checkout ({repo_path}).{shared}"""

SHARED = ("\nShared with the main checkout (linked, not copied — a change there is a change "
          "everywhere): {names}.")

READONLY = ("\nThis item is readonly: commit nothing and change no tracked file. "
            "Conductor checks the repo afterwards and marks a change red.")
# Not for a watch: an item doing real work, which may be told to record or commit something (aicomplete 109).
READONLY_CARRY = (" Nothing merges, so seed-ledger entries and anything the prompt says to commit go "
                  "in `carry:` for the planner.")

READONLY_TREE = READONLY.lstrip()      # says all a worktree item needs: it never commits or merges

# The full preamble's working papers (*The box*) and warning-checks clause; a readonly item
# commits nothing and measures nothing it touched, so it gets its own (146, helium-hustle 045).
PAPERS = ("Working papers the prompt asks for go\nunder the repo's results folder "
          "(`experiments/results/{item_name}/`), committed; read outputs\na later item could reuse go in "
          "`out/keep/`, kept at `{kept}` for your report to name.")
PAPERS_READONLY = ("Working papers the prompt asks for, and read\noutputs a later item could reuse, go in "
                   "`out/keep/{item_name}/` (nothing you write commits; `out/keep/`\nis kept at `{kept}`), "
                   "named in your report.")
RUN_CHECKS = "run them before the report; no such\nsection is fine"
RUN_CHECKS_READONLY = ("readonly: skip them and write `warnings: as on main`,\nunless you ran a check your "
                       "read depends on: then what it tripped")
# The light preamble has no *The box*: a readonly light item gets both in one line.
READONLY_LIGHT = (" Working papers go in `out/keep/{item_name}/`, named in your report. Skip the warning "
                  "checks (`warnings: as on main`) unless you ran one your read depends on.")

PATHS = ("\nThis item declared `paths:` {globs}. Change nothing outside them: conductor "
         "checks what the branch changed and marks anything else red, unmerged.{prose}")

# `write_roots` (conductor.toml; default its `open_roots`): folders outside the repo items
# routinely write. fractals 050 left `style gallery` unrun, believing it might write one path.
WRITES = ("\nWritable outside the repo: {roots} (the project's `write_roots`). Write there as the "
          "work needs (keeps, galleries, sync output); `paths:` covers only the repo.")
WRITES_LIGHT = "\nWritable outside the repo: {roots}."

PORT_VAR = "CONDUCTOR_PORT"

PORT_LIGHT = ("\nPort. A server you start uses port {port} (`" + PORT_VAR + "`), passed through where a "
              "script hard-codes one.")

# Warnings when an item starts beside others in its repo and either is `alongside`: a
# paragraph in its prompt, and a message (the `tell:` path) to each one already running.
BESIDE = ("\nBeside you. Conductor started you beside other items in this repo, each in its own "
          "worktree and merging on its own:\n{items}\nThey are likely unrelated to yours: stay out "
          "of their areas, and say in your report if you had to touch them.")
BESIDE_TELL = ("{item} started beside you: {gist}. Likely unrelated; avoid those files, and say in your "
               "report if you had to touch them.")

RUNNING_NOW = "\nRunning beside you now: {items}."

PROSE = (" Prose files ({globs}) are exempt: update them as the work needs; another item may "
         "edit them at the same time, and conductor merges them textually.")

STAGE = """\
You are a conductor executor in its staging phase: an unattended headless Claude Code
session for queue item {item_id} of project {project}. Nobody is watching the session.
Another item holds this repo's lock, so you get a head start while you wait.

{workdir}

While staging, read the prompt below and whatever it points at, with read-only commands only
(nothing that writes, commits, installs or deletes) and nothing heavy (no builds, test
suites, servers or GPU work), {stage_minutes:g} minutes at most. There is no ask tool yet: list
questions in your plan under a line `Questions:`, one `- ` bullet each, ending
`(default: <what you will do unanswered>)`; they reach the planner before you launch.

Then write your plan as your final message, ending with its outline: your sections, a few
words each, the report last. Stop. When the lock is yours, conductor resumes this session in
this worktree, moved to the then-current `{main}`, with the instructions for executing.

The prompt follows.

---

"""

RESUME_HEAD = """\
You have the lock now. Conductor moved your worktree from {old} to the current `{main}`
at {new}{landed}. Check what landed since you staged and adjust your plan, re-deriving
any time, rate or baseline the prompt quotes from it, then execute the prompt you read
while staging. Everything below now applies.

"""

STAGE_DEFAULTS = """\
Staging questions unanswered: defaults stand. Take the default you named for each; where one
shapes the work (its cost, a bar, a design), say so in your report, since the planner sees
them as rulings still open:
{lines}
{stale}
"""

# After STAGE_DEFAULTS when main moved between staging and launch (142: aicomplete 292 took
# the default for "`docs/rulings.md` does not exist" with the file on main by launch).
STAGE_STALE = """Main moved since you staged ({n} commit(s)): first re-check each question's premise on
the tree you have now. A file a question calls missing may exist; a question the tree
answers is no longer open: follow the tree, not the default, and say so in your report.
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

EXTERNAL = """

---

Data outside git. This item changes data outside the repo, which no merge can undo. Commit
the code (and any progress or state file) before each step that changes that data, and
record in a committed file how far each step got, so that if you are cut off, your branch
matches the data and can land as it is.
"""

STAGE_AMENDS = """
---

Amendments already sent for this item: each amends the prompt above and wins where they
conflict. Plan with them; they come again with the resume message.
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


def _where(cfg: Config, item: Item, run: dict, light: bool = False, watch: bool = False) -> str:
    """The preamble's working-directory lines. A readonly item in a worktree gets only the
    readonly line: the commit and merge rules are for work that lands."""
    if not item.readonly:
        return _workdir(cfg, item, run, light)
    carry = "" if watch else READONLY_CARRY + (READONLY_LIGHT.format(item_name=item.name) if light else "")
    if not run.get("worktree"):
        return _workdir(cfg, item, run, light) + READONLY + carry
    shared = SHARED.format(names=", ".join(f"`{s}`" for s in run["shared"])) if run.get("shared") else ""
    return READONLY_TREE + carry + shared


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
OPTIONAL_KEYS = ("slow", "friction", "warnings", "partial", "pick")     # straight after the three, any order
TEXT_KEYS = ("slow", "friction", "warnings", "partial")     # kept as written; `pick` is a path
MAX_PICKS = 12                                   # `pick:` lines a header may carry, one per picker
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
                 resumed: str = "", parked: list[dict] = (), choices: str = "",
                 defaults: list[str] = ()) -> str:
    """The execution preamble and the item body; with `resume` (old/new base, landed
    count), the message that resumes a staged session instead. `notes`: a cleanup's
    audit notes, inlined; `usage`: a disk cleanup's control-folder sizes and worktrees;
    `amends`: amendments written before the launch, appended in order; `beside`: the
    (id, gist) of each item it is warned about starting beside (`BESIDE`); `resumed`: a
    resume item's paragraph on its original (`resume.paragraph`); `parked`: the parked edits
    it carries (`parked.section`); `choices`: a `needs_pick` item's saved choices
    (`picks.section`); `defaults`: its stage's unanswered questions (`STAGE_DEFAULTS`)."""
    run = run or {}
    prose = cfg.opts(item.repo).prose
    keeps = audits.writes_notes(item.meta())
    watch = item.template == WATCH_TEMPLATE
    light = is_light(item)
    pick = not light and not watch and wants_pick(item)
    running = run.get("running_beside")
    serves = not watch and bool(run.get("port")) and has_servers(cfg, item, run)
    writes = ((WRITES_LIGHT if light else WRITES).format(roots=", ".join(f"`{p.as_posix()}`" for p in cfg.write_roots))
              if cfg.write_roots and not item.readonly and not watch else "")
    only = ONLY_TWO if keeps else ONLY_LIGHT if light else ONLY_ONE
    rulings = not watch and has_rulings(cfg, item, run)
    if writes:
        only = only[:-1] + ROOTS_TOO + "." if only.endswith(".") else only + ROOTS_TOO
    text =(WATCH if watch else LIGHT if light else PREAMBLE).format(
        item_id=item.id, item_name=item.name, project=cfg.project, workdir=_where(cfg, item, run, light or watch, watch),
        threads=load.run_threads(),
        papers=(PAPERS_READONLY if item.readonly else PAPERS).format(item_name=item.name,
                                                                     kept=kept.where(cfg, item, run).as_posix()),
        run_checks=RUN_CHECKS_READONLY if item.readonly else RUN_CHECKS,
        paths=PATHS.format(globs=_ticks(item.paths), prose=PROSE.format(globs=_ticks(prose)) if prose else "")
        if item.paths else "",
        writes=writes,
        port=PORT_LIGHT.format(port=run["port"]) if light and serves else "",
        calibrate=CALIBRATE if wants_calibrate(item) else "",
        now=RUNNING_NOW.format(items=", ".join(running) or "nothing") if running is not None else "",
        leftover="" if item.readonly or item.template == HUNT_TEMPLATE else LEFTOVER_LIGHT if light else LEFTOVER_FULL,
        beside=BESIDE.format(items="\n".join(f"- {i}: {g}" for i, g in beside)) if beside else "",
        resumed=resumed,
        audit="\n" + audits.fill(audits.template_text("audit"), notes_path=str(paths.audit.resolve()))
        + (AUDIT_FIX if item.audit and not item.readonly and not item.template else "") if keeps else "",
        only=only,
        review=review_text(item, run, light or watch, rulings),
        rulings=RULINGS_FULL if rulings else "",
        rulings_light=RULINGS_LIGHT if rulings else "",
        calls=calls_text(cfg, item.repo, light or watch),
        servers=SERVERS.format(port=run["port"]) if not light and serves else "",
        max_hours=max_hours, ask_minutes=cfg.ask_default_minutes,
        deadline=clock(deadline), report=paths.report.resolve(),
        pick=PICK if pick else "", pick_line=PICK_LINE if pick else "",
        checks=checks_text(cfg.opts(item.repo).warning_checks),
        tail="" if resume else PROMPT_FOLLOWS,
    )
    extra = (choices + parked_mod.section(list(parked)) + (EXTERNAL if item.external_data else "")
             + ("" if watch else kept.section(cfg, item.repo)))
    if not resume:
        return text + body(cfg, item, notes, usage) + extra + "".join(_amend(AMEND, m) for m in amends)
    n = resume["landed"]
    landed = (f"; {n} commit(s) landed since you staged (`git log --oneline {resume['old'][:10]}..HEAD`)"
              if n else "; nothing landed since you staged")
    head = RESUME_HEAD.format(old=resume["old"][:10], new=resume["new"][:10], main=cfg.opts(item.repo).main,
                              landed=landed)
    if defaults:
        head += STAGE_DEFAULTS.format(lines="\n".join(questions.default_lines(list(defaults), "")),
                                      stale=STAGE_STALE.format(n=n) if n else "")
    tail = (RESUME_AMENDS + "".join(_amend(RESUME_AMEND, m) for m in amends)) if amends else ""
    return head + text.rstrip() + "\nThe prompt is the one you read while staging.\n" + extra + tail


def review_text(item: Item, run: dict, short: bool = False, rulings: bool = False) -> str:
    """The full preamble's *Diff review* (123): only a writable item that isn't a template,
    against the commit it started from (its worktree's base, or the main checkout's head);
    with `rulings`, the brief carries the repo's `docs/rulings.md` (137)."""
    if short or item.template or item.readonly:
        return ""
    base = (run.get("base") or run.get("repo_head") or "")[:10] or "<the commit you started from>"
    if item.ultracode:
        return REVIEW_ULTRA.format(base=base, rulings=f", checked against `{RULINGS}` too" if rulings else "")
    return REVIEW.format(base=base, rulings=f" the repo's `{RULINGS}` whole," if rulings else "")


def has_rulings(cfg: Config, item: Item, run: dict) -> bool:
    """The repo keeps its standing rulings in `docs/rulings.md` (137): in the item's worktree,
    else its main checkout."""
    root = Path(run.get("worktree") or cfg.repos[item.repo])
    return (root / RULINGS).is_file()


def checks_text(commands: list[str]) -> str:
    """The repo's `warning_checks` (conductor.toml) for the preamble's `warnings:` line:
    `` and `a`; `b` ``, or nothing."""
    return " and " + "; ".join(f"`{c}`" for c in commands) if commands else ""


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
    """The *Servers* paragraph (and with it the port) goes in when the repo has a run target,
    or a `package.json` at its root or one folder down (aicomplete's `web/`) has a dev or
    preview script (`dev`, `dev:web`, `preview`)."""
    repo = cfg.repos[item.repo]
    if any(Path(t.cwd).resolve().is_relative_to(Path(repo).resolve()) for t in cfg.targets.values()):
        return True
    root = Path(run.get("worktree") or repo)
    return any(_dev_scripts(p) for p in (root / "package.json", *root.glob("*/package.json"))
               if "node_modules" not in p.parts)


def _dev_scripts(path: Path) -> bool:
    try:
        scripts = json.loads(read_text(path)).get("scripts") or {}
    except (OSError, ValueError, AttributeError):
        return False
    return isinstance(scripts, dict) and any(DEV_SCRIPT_RE.match(k) for k in scripts)


def wants_calibrate(item: Item) -> bool:
    """The calibration line goes in for a `rules: true` item or a body naming a gate, band,
    target or seed count."""
    return item.rules or bool(CALIBRATE_RE.search(item.body))


def build_stage_prompt(cfg: Config, item: Item, run: dict, choices: str = "", amends: list[dict] = ()) -> str:
    """`amends`: amendments still waiting (a rewrite discarded the stage they were sent to)."""
    tail = (STAGE_AMENDS + "".join(_amend(RESUME_AMEND, m) for m in amends)) if amends else ""
    return STAGE.format(item_id=item.id, project=cfg.project, workdir=_workdir(cfg, item, run),
                        stage_minutes=cfg.stage_minutes, main=cfg.opts(item.repo).main) + body(cfg, item) + choices + tail


def body(cfg: Config, item: Item, notes: str = "", usage: str = "") -> str:
    """What follows the preamble: the item body, or for a `template:` item the
    template's text (a cleanup's with its audit notes inlined, a disk cleanup's with
    conductor's measurements) and then whatever the planner wrote."""
    if not item.template:
        return item.body
    text = audits.fill(audits.template_text(item.template), repo=item.repo, project=cfg.project,
                       notes=notes.strip(), usage=usage.strip(), original=item.resume_of or "").rstrip() + "\n"
    if item.template == "self_audit":
        text += LEFTOVER_AUDIT
    elif item.template not in (WATCH_TEMPLATE, HUNT_TEMPLATE) and not item.readonly:
        # a watch never has an hour left; a bug hunt stops on yield (137)
        text += LEFTOVER
    if item.body.strip():
        who = "Why conductor launched this watch" if item.template == WATCH_TEMPLATE else "The planner adds, for this item"
        text += f"\n---\n\n{who}:\n\n{item.body}"
    return text


def mcp_config(cfg: Config, item: Item, session_id: str, workdir: Path | str) -> str:
    """The executor's --mcp-config: the ask server, run by the daemon's own interpreter."""
    from .ask_server import SERVER_NAME
    return json.dumps({"mcpServers": {SERVER_NAME: {
        "type": "stdio", "command": sys.executable, "timeout": ASK_TIMEOUT_MS, "alwaysLoad": True,
        "args": ["-m", "conductor.ask_server", "--control", str(cfg.control), "--item", item.id,
                 "--default-minutes", f"{cfg.ask_default_minutes:g}", "--session-id", session_id,
                 "--workdir", str(workdir)],
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
    names, flags = [], []
    try:
        from . import remote             # optional (docs/remote-workers.md): a `remote` item's farm reminder
        if remote.wants(cfg.control, item):
            names, flags = remote.native_hint(cfg.control)
    except ImportError:
        pass
    data = json.loads(settings(console_python(), cfg.control, item.id, cfg.foreground_seconds,
                               cfg.opts(item.repo).background, readonly=item.readonly, native=names, flags=flags))
    if item.ultracode:
        data["ultracode"] = True
    return json.dumps(data, indent=1)


def command(cfg: Config, session_id: str, mcp: Path | None, resume: bool = False,
            settings: Path | None = None, fork_of: str | None = None, model: str | None = None) -> list[str]:
    """`--session-id` for a new session; `--resume` continues a staged one; `fork_of`
    (a resume item) resumes that finished session as a fork under `session_id`, leaving
    the original as it was. A stage gets no --mcp-config (it may not ask) and no
    --settings (nobody messages a stage). `model`: the full id (`models.py`); a resume
    passes the one its session was launched with, None (a run from before 135) passes none."""
    from .procs import resolve
    session = (["--resume", fork_of, "--fork-session", "--session-id", session_id] if fork_of
               else ["--resume" if resume else "--session-id", session_id])
    return [resolve(cfg.command[0]), *cfg.command[1:], "-p", "--dangerously-skip-permissions",
            "--output-format", "stream-json", "--verbose", *session, *(["--model", model] if model else []),
            *(["--mcp-config", str(mcp)] if mcp else []), *(["--settings", str(settings)] if settings else []),
            *cfg.args]


def read_header(report: Path) -> tuple[dict | None, str | None]:
    """(header, problem). Accepts the three lines bare or inside a `---` block, then
    `slow:` (what took long), `friction:` (what cost the item time or tokens), `warnings:`
    (what its checks tripped; absent is no problem either), `partial:` (why each unmet bar
    is unmet, `matt:` leading one that waits on his call: docs/retros.md) and optional `pick:` lines (a picker manifest's path each: `pick` is the
    first, `picks` all of them; fractals 022 had three). Those only right after the three,
    in any order, never in the body."""
    if not report.is_file():
        return None, "no report written"
    lines = [ln.strip() for ln in read_text(report).splitlines()]
    lines = [ln for ln in lines if ln and ln != "---"][:3 + len(OPTIONAL_KEYS) + MAX_PICKS]
    header = {}
    seen = 0                            # optional lines met so far, set or not
    for n, ln in enumerate(lines):
        m = re.match(r"^\**(status|blocks|carry|slow|pick|friction|warnings|partial)\**\s*:\s*(.*)$", ln, re.IGNORECASE)
        key = m.group(1).lower() if m else None
        if key is None or (key in header and key != "pick"):
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
        if key in TEXT_KEYS:
            if value:
                header[key] = value
            continue
        # `pick:` as one path: a line that merely starts "Pick: went with the warmer
        # palette" is the body's.
        value = value.strip("`").strip()
        if value and len(value.split()) == 1 and len(header.setdefault("picks", [])) < MAX_PICKS:
            header.setdefault("pick", value)
            if value not in header["picks"]:
                header["picks"].append(value)
    if header.get("picks") == []:
        del header["picks"]
    missing = [k for k in HEADER_KEYS if k not in header]
    if missing:
        return header or None, f"report header missing {', '.join(missing)}"
    status = header["status"].lower().split()[0] if header["status"] else ""
    if status not in STATUSES:
        return header, f"report status {header['status']!r} is not done/partial/red"
    header["status"] = status
    return header, None
