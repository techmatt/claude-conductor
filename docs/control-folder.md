# The control folder

## Control folder layout

```
conductor.toml                 tracked — project, repos, locks, defaults
state.md, other docs           tracked — changed only through requests
.gitignore, .gitattributes     tracked — must ignore every runtime path below
queue/<repo>/NNN_name.md       approved work, run in NNN order
queue/<repo>/NNN_name.amend.md an amendment to that item (`.amend.<anything>.md` too); never an item
proposed/NNN_name.md           awaiting approval (shown in status/inbox, never run)
running/<repo>/NNN_name.md     the item while it runs or waits to merge, plus NNN_name.run.json
running/<repo>/NNN_name.stage.json   a staged queue head (the item itself stays in queue/)
done/<repo>/NNN_name.md        finished items, plus their .run.json
reports/<repo>__NNN_name.md    the executor's report
reports/<repo>__NNN_name.log   raw executor output (stream-json); also .stderr.log, .prompt.md,
                               .mcp.json, .settings.json, .setup.log, and .stage.log /
                               .stage.prompt.md / .stage.stderr.log for a stage; .merge.log /
                               .merge.prompt.md / .merge.stderr.log for a merge executor;
                               .audit.md (audit notes);
                               .amend.md (every amendment delivered, in order, with times)
requests/<stamp>/              planner → conductor: replacement files + request.md
signals/                       planner/app → conductor: the verbs in signals.md
receipts/                      conductor → planner: what became of each write (see receipts.md)
backlog/<repo>/<name>.md       pre-approved items, drawn into an empty queue (see night-shift.md); ignores itself
questions/                     executor asks: <id>.md, <id>.answer.md, <id>.result.json
picks/                         Matt's saved picker choices: <name>.choices.json (see picks.md)
inbox.md                       generated digest since the read mark
session.md                     the start digest the last `session:` wrote (see inbox.md)
review.md                      the review bundle the last `review:` wrote (see inbox.md)
events.db                      append-only event log (SQLite, WAL)
.conductor/                    the daemon's own state: lock, heartbeat, journals (see runtime.md)
```

A run record's `phase` is `setup`, `exec`, `merge` or `concluding`. Run and stage
records carry `setup` (state `running | ok | failed | killed`, commands, spec, result,
log, pid, create_time, started, finished, error); a stage record stays `phase: staging`
with `pid: null` until its staging executor spawns. A run record from a staged resume
carries `resume` (old, new, landed).

Conductor creates the runtime folders on start and logs a `warning` event if any of
them is not git-ignored (a folder made before a runtime path existed, e.g. `review.md`,
warns `not git-ignored in the control folder: ['review.md']` until a request adds the
line). `conductor init` (below) writes all of this. A suitable `.gitignore`:

```
queue/
proposed/
running/
done/
reports/
questions/
picks/
requests/
signals/
receipts/
backlog/
inbox.md
session.md
review.md
events.db*
.conductor/
```

## conductor.toml

```toml
project = "sandbox"
locks = ["shared"]            # named locks items may take; must not clash with repo names
worktree_root = "../conductor-sandbox-worktrees"   # default ../<project>-worktrees
open_roots = ["C:/Code/gdrive-sync/sandbox"]  # more folders `open:` may act in (see signals.md)

[defaults]
max_hours = 2                 # per item unless the item sets max_hours
poll_seconds = 2              # daemon loop interval
ask_default_minutes = 10      # an unanswered non-blocking ask returns its default after this
stage_minutes = 30            # a stage running longer is killed and discarded
setup_minutes = 10            # per setup command
merge_minutes = 10            # a merge executor resolving a prose conflict is killed after this
foreground_seconds = 60       # an executor's foreground shell call moves to the background after this; 0 = off (see progress.md)
toasts = true                 # Windows desktop notifications (see inbox.md, Toasts)
memory_sample_seconds = 30    # memory sample and heartbeat interval; 0 = off (see memory.md)
memory_keep_hours = 24        # samples kept in .conductor/memory.jsonl
memory_pressure_percent = 90  # pressure: commit at or over this % of its limit (0 = off) ...
memory_pressure_free_gb = 3   # ... or free RAM at or under this (0 = off)
memory_critical_percent = 95  # critical: the same, further on
memory_critical_free_gb = 1.5
memory_calm_seconds = 300     # a level falls only after the box stays clear this long (a rise is at once)
# the night shift's keys: night-shift.md, Settings; run targets ([run.<name>]): run-targets.md

[repos]                       # name = path; relative paths resolve from the control folder
sandbox = "../conductor-sandbox"

[repos.app]                   # or a table, for anything beyond the defaults
path = "../app"
worktrees = true              # false: run in the main checkout, one item at a time (phase 1)
stage = true                  # stage the queue head while the repo is busy (needs worktrees)
main = "main"                 # the branch items are cut from and merged into
push = false                  # true: push main to the remote after each merge (see push.md)
shared = ["data", ".venv"]    # untracked folders linked into every worktree (junctions)
setup = ["uv sync"]           # shell commands run in a new worktree before launch
prose = ["CLAUDE.md", "README.md", "docs/**/*.md"]   # the default: exempt from `paths`, merged textually
background = ["npm test*", "*playwright test*"]       # shell commands executors always background (default none; see progress.md)

[executor]                    # optional
command = ["claude"]          # the executable (resolved on PATH)
args = []                     # extra arguments, e.g. ["--model", "opus"]
```

Repo names may not contain `__` or slashes (report names are `<repo>__<item>.md`).
`worktree_root` must lie outside the control folder and every repo; worktrees live at
`<worktree_root>/<repo>/<NNN_name>`. Unknown keys in a repo table are an error.
A request that changes `conductor.toml` reloads it; an invalid one is logged red and
the previous config stays in force.

## conductor init

```
conductor init C:\Code\<project>-control --project <project> --repo app=C:\Code\app [--repo …]
```

creates the folder with the layout above: `conductor.toml` (the project, a `[repos.<name>]`
table per repo with `push = false` and its path — relative like `../app` when beside or
under the control folder, else absolute —, `max_hours = 2`,
`ask_default_minutes = 10`), a stub `state.md`, `.gitignore` covering every runtime
path, `.gitattributes` (`* text=auto eol=lf`), the runtime folders and a queue folder
per repo; then `git init -b main` and a first commit of the four tracked files. An
existing empty folder is fine (Matt may create it first to grant access); a non-empty
one is refused, never overwritten. A repo path that is not a git repo yet is a warning,
not an error: its queue holds until it is one.

## Control-folder guard

The guard's snapshot (`.conductor/snapshot/`) holds every tracked file's bytes, every
ref, symbolic HEAD and the index tree. It is persisted, so an item adopted after a
restart is judged against the snapshot it launched under.

- Before a launch while **no executor is running**, the folder is compared with the
  snapshot; a difference is `drift` (made outside an executor's window — accepted,
  logged, re-snapshotted).
- Whenever the folder is checked while **an executor is running** — at every executor
  exit, before every launch, before every request — any difference (a modified,
  deleted or newly tracked file, a new commit, a moved ref or branch, a staged change)
  is reverted to the snapshot and logged as a red `tamper` event naming the item. The
  item ends red. When several executors were running, the event lists them all
  (`concurrent`). It is pinned on those whose own writing tool calls (`Write`, `Edit`,
  a shell command…, the lead's or a helper's) named a changed path since the snapshot:
  only they end red (`attributed`). The message hook notes the control-folder paths
  each call names in `.conductor/touch/<repo>__<NNN_name>.jsonl` (`talk.touch_paths`:
  a file tool's target, every path in a shell command, reads too; never `reports/` or
  `.conductor/`, so writing its report is no evidence). A path covers a change of
  itself or a file under it; the bare folder (`git -C <control> …`) covers only a
  commit, ref, HEAD or index change (`guard.named`). When no item's calls cover the
  change (a change from outside, a script or relative path that never names the folder,
  a stage or merge executor, which runs no hook, a call still running at the check),
  each running item ends red, as conductor cannot tell which did it.
- Untracked files are not guarded (the report itself is one).
- Tamper is the first entry of the inbox's Red section.

The guard is the backstop, not the first line: the preamble forbids touching the
control folder, and in the sandbox executors refused even when the item body told
them to edit `state.md`, told them to run a script that does, or committed through a
hook that does (the executor bypassed the hook). Tamper by an executor itself is
therefore exercised in the fake-executor tests; end to end it was proven with a
change made from outside during a live executor's window.

## Requests

The only way a tracked control file changes. The planner creates
`requests/<stamp>/` holding whole replacement files at their paths relative to the
control folder, plus `request.md`, all in one bridge call, in any order:

```
---
targets: [state.md, docs/plan.md]
message: Optional one-line commit message
---
Commit message (used when `message:` is absent; first line is the subject).
```

The front-matter is YAML: a `message:` containing a colon must be quoted
(`message: "state: v0.2"`). Unquoted, the YAML fails and the request lands `.failed`.

Conductor waits until nothing in the folder has changed for 2 seconds
(`SETTLE_SECONDS`) and every target is present, checks the guard, copies the files in
(UTF-8 text is written with LF endings; anything else byte for byte), stages and
commits exactly the targets, logs `request` and `commit`, removes the request folder
and re-snapshots. Targets not there yet are waited for, with no event, until 60s
(`REQUEST_WAIT_SECONDS`) after `request.md`'s mtime (so the wait survives a restart;
rewriting `request.md` starts it again); then the request fails (`targets listed but
not in the request folder after waiting 60s: [...]`). Every other error fails at once:
bad front-matter, no `targets:`, a target that is absolute, uses `..` or points into
the runtime paths or `.git`, no commit message. Every file in the folder must be a
target, checked once nothing is missing. A target that lands while the folder is being
checked sends it round once more (it is never applied half-written). Requests keep stamp
order where their targets overlap: a later one naming a target of an earlier, waiting
one waits too. A bad request is logged `request_failed` (red)
and renamed `requests/<stamp>.failed/`. A request that changes nothing is logged with
`no_change` and removed. Requests apply in folder-name order; the planner names them
`YYYYMMDD-HHMM-<tag>`. One whose message starts `closeout:` also moves the read mark
(`inbox.md`). Applied or failed, a request's outcome is `receipts/requests/<stamp>.md` (`receipts.md`).
