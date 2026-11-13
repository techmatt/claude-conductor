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
reports/<repo>__NNN_name.log   raw executor output (stream-json; .log.gz once finished, as are
                               .stage.log and .merge.log, logs.md); also .stderr.log, .prompt.md,
                               .mcp.json, .settings.json, .setup.log, and .stage / .merge
                               .log, .prompt.md, .stderr.log for a stage or merge executor;
                               .audit.md (audit notes); .original.md (resume.md);
                               .amend.md (each amendment delivered, with times)
requests/<stamp>/              planner → conductor: request.md (+ replacement files, or edits)
signals/                       planner/app → conductor: the verbs in signals.md
receipts/                      conductor → planner: what became of each write (see receipts.md)
backlog/<repo>/<name>.md       pre-approved items, drawn into an empty queue (see night-shift.md); ignores itself
parked/<repo>[.done].md        parked edits for the next serial item (parked.md); ignores itself
questions/                     executor asks: <id>.md, .answer.md, .result.json, .away
picks/                         Matt's saved picker choices: <name>.choices.json (see picks.md)
inbox.md                       generated digest since the read mark
session.md                     the start digest the last `session:` wrote (see inbox.md)
review.md                      the review bundle the last `review:` wrote (see inbox.md)
remote-diag.md                 remote workers' diagnostics, with `[remote]` (remote-diag.md)
events.db                      append-only event log (SQLite, WAL)
.conductor/                    the daemon's own state: lock, heartbeat, journals (see runtime.md)
```

A run record's `phase` is `setup`, `exec`, `merge` or `concluding`. Run and stage
records carry `setup` (state `running | ok | failed | killed`, commands, spec, result,
log, pid, create_time, started, finished, error); a stage record stays `phase: staging`
with `pid: null` until its staging executor spawns. A run record from a staged resume
carries `resume` (old, new, landed).

Conductor creates the runtime folders on start and logs a `warning` event if any of
them is not git-ignored (`not git-ignored in the control folder: ['review.md']`) until
a request adds it. `conductor init` (below) writes all of this. A suitable `.gitignore`:

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
remote-diag.md
events.db*
.conductor/
```

## conductor.toml

```toml
project = "sandbox"
display_name = "Sandbox"      # session.md's "<display name> session N" (inbox.md); default: project capitalised
locks = ["shared"]            # named locks items may take; must not clash with repo names
worktree_root = "../conductor-sandbox-worktrees"   # default ../<project>-worktrees
open_roots = ["C:/Code/gdrive-sync/sandbox"]  # more folders `open:` may act in (see open.md)

[defaults]
max_hours = 2                 # per item unless the item sets max_hours
poll_seconds = 2              # daemon loop interval
ask_default_minutes = 10      # an unanswered non-blocking ask returns its default after this
stage_minutes = 30            # a stage running longer is killed and discarded
setup_minutes = 10            # per setup command
merge_minutes = 10            # a merge executor resolving prose is killed after this
foreground_seconds = 60       # a foreground shell call is backgrounded after this; 0 = off (progress.md)
toasts = true                 # Windows desktop notifications (inbox.md, Toasts)
doc_budget_kb = 15            # a repo doc past this is **due** in session.md (inbox.md)
auto_retros = true            # queue a due workflow retro unasked (retros.md)
api_retry_minutes = [2, 10, 30]  # an API failure resumes the session after each wait; [] = red at once (api-retry.md)
memory_sample_seconds = 30    # memory sample and heartbeat interval; 0 = off (memory.md)
memory_keep_hours = 24        # samples kept in .conductor/memory.jsonl
memory_pressure_percent = 90  # pressure: commit at or over this % of its limit (0 = off) ...
memory_pressure_free_gb = 3   # ... or free RAM at or under this (0 = off)
memory_critical_percent = 95  # critical: the same, further on
memory_critical_free_gb = 1.5
memory_calm_seconds = 300     # a level falls only once the box is clear this long (rises at once)
load_warn_free_threads = 2    # load_high: under this many hardware threads free ...
load_warn_seconds = 180       # ... for this long (see memory.md)
load_tell_minutes = 15        # how often a stretch logs again, and an item causing it is messaged at most
# night shift keys: night-shift.md; [run.<name>]: run-targets.md; [tools.<name>]: app.md; [remote]: remote-workers.md

[links]                       # name = URL; session.md lists them first, above everything else
"design doc" = "https://claude.ai/artifact/…"

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
append_only = ["experiments/history.md"]            # logs only appended to: merged by union on landing (default none; worktrees.md)
background = ["npm test*", "*playwright test*"]       # shell commands executors always background (default none; progress.md)
doc_budget_exempt = ["docs/cards.md"]                 # docs never listed as trim due (inbox.md)
warning_checks = ["uv run python tools/design_check.py"]  # commands every executor runs for its `warnings:` line (warnings.md)

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

creates the folder: `conductor.toml` (the project; per repo a `[repos.<name>]` table, its
path (relative, `../app`, beside it), `push = false`; `max_hours = 2`,
`ask_default_minutes = 10`), a stub `state.md` (every section `planner.md` expects, in
order; *Maintenance* `never` per template and repo), `.gitignore`,
`.gitattributes`, the runtime and queue folders; then `git init -b main` and a first
commit. A non-empty folder is refused (an empty one, made to grant access, is fine).

Each repo first (`scaffold.prepare_repo`, printed): missing or not a repo of its own:
`git init -b main`, an empty commit, no remote; no commits: one on `main`; on another
branch, no `main`: `main = "<branch>"`. Until a repo has a remote, `session.md` (under
`Repos:`) and the app's repo header say `<repo> has no remote: create its GitHub repo`.

## Control-folder guard

The guard's snapshot (`.conductor/snapshot/`) holds every tracked file's bytes, every
ref but remote-tracking ones, symbolic HEAD and the index tree. It is persisted, so an
item adopted after a restart is judged against the snapshot it launched under.
`refs/remotes/*` (what a remote holds; conductor's own push moves them) are never drift,
tamper or reverted.

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

The guard is the backstop: the preamble forbids touching the control folder, and in
the sandbox executors refused even when told to. So executor tamper is exercised in the
fake-executor tests; end to end, with a change from outside during a live run.

## Requests

The only way a tracked control file changes: the planner's `requests/<stamp>/` folders, how
they apply, wait, fail and are ordered, and edit blocks are in `requests.md`.
