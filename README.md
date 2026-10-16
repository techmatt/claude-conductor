# claude-conductor

Conductor is a daemon that runs queued Claude Code prompts for one project. It is
pointed at the project's **control folder**, runs approved prompts from per-repo
queues as headless `claude -p` sessions (each in its own git worktree, merged into
`main` when it finishes; one at a time per repo unless items declare disjoint
`paths`), and records
everything in an append-only event log from which the inbox and every other view is
derived. `claude-ai-new-project.md` is how a project uses it; this file is how it
works and is the canon for commands, file formats, fields and event kinds.

## Install and run

```
uv sync
uv run conductor run    <control folder>   # the daemon, foreground; Ctrl+C stops it
uv run conductor status [<control folder>]
uv run conductor inbox  [<control folder>]
uv run conductor events [<control folder>] [--since X] [--item Y] [--repo R] [--kind K] [--paths]
uv run conductor questions [<control folder>] [--all]      # open asks (--all: resolved too)
uv run conductor answer <id> <text…> [--control <folder>]  # writes the answer file
uv run conductor init <control folder> --project NAME --repo NAME=PATH [--repo …]
uv run conductor app [<control folder> …]                  # the desktop app (see The app)
```

The control folder argument defaults to `$CONDUCTOR_CONTROL`, then the current folder.
`conductor app` with no folder adds `$CONDUCTOR_CONTROL` if set, else the current folder
only when it holds a `conductor.toml`; it always opens its remembered projects too.
One daemon per control folder: a second `run` refuses while the first is alive
(`.conductor/daemon.json` holds its pid and process creation time).

`--since` takes an event id (`#12` or `12`, exclusive), a relative age (`30m`, `2h`,
`1d`), a clock time today (`5:25pm`), or an ISO time. `--item` takes `sandbox/001_x`,
`001_x` or `001`.

Stopping the daemon (Ctrl+C, or killing it) does **not** stop executors: they run
detached (no console window, own process group), writing straight to their log files.
The next start adopts them — see *Restart rule*.

## Control folder layout

```
conductor.toml                 tracked — project, repos, locks, defaults
state.md, other docs           tracked — changed only through requests
.gitignore, .gitattributes     tracked — must ignore every runtime path below
queue/<repo>/NNN_name.md       approved work, run in NNN order
proposed/NNN_name.md           awaiting approval (shown in status/inbox, never run)
running/<repo>/NNN_name.md     the item while it runs or waits to merge, plus NNN_name.run.json
running/<repo>/NNN_name.stage.json   a staged queue head (the item itself stays in queue/)
done/<repo>/NNN_name.md        finished items, plus their .run.json
reports/<repo>__NNN_name.md    the executor's report
reports/<repo>__NNN_name.log   raw executor output (stream-json); also .stderr.log, .prompt.md,
                               .setup.log, and .stage.log / .stage.prompt.md for a stage
requests/<stamp>/              planner → conductor: replacement files + request.md
signals/                       planner/app → conductor: read, clear, approve, cancel, reorder, reviewed, stop
questions/                     executor asks: <id>.md, <id>.answer.md, <id>.result.json
inbox.md                       generated digest since the read mark
events.db                      append-only event log (SQLite, WAL)
.conductor/                    daemon lock (daemon.json), guard snapshot, cancelled/ (kept
                               cancelled items), reorder.json and signal.json (crash journals),
                               daemon.out.log (a daemon started by the app)
```

Conductor creates the runtime folders on start and logs a `warning` event if any of
them is not git-ignored. `conductor init` (below) writes all of this. A suitable `.gitignore`:

```
queue/
proposed/
running/
done/
reports/
questions/
requests/
signals/
inbox.md
events.db*
.conductor/
```

## conductor.toml

```toml
project = "sandbox"
locks = ["shared"]            # named locks items may take; must not clash with repo names
worktree_root = "../conductor-sandbox-worktrees"   # default ../<project>-worktrees

[defaults]
max_hours = 2                 # per item unless the item sets max_hours
poll_seconds = 2              # daemon loop interval
ask_default_minutes = 10      # an unanswered non-blocking ask returns its default after this
stage_minutes = 30            # a stage running longer is killed and discarded
setup_minutes = 10            # per setup command
toasts = true                 # Windows desktop notifications (see Toasts)

[repos]                       # name = path; relative paths resolve from the control folder
sandbox = "../conductor-sandbox"

[repos.app]                   # or a table, for anything beyond the defaults
path = "../app"
worktrees = true              # false: run in the main checkout, one item at a time (phase 1)
stage = true                  # stage the queue head while the repo is busy (needs worktrees)
main = "main"                 # the branch items are cut from and merged into
shared = ["data", ".venv"]    # untracked folders linked into every worktree (junctions)
setup = ["uv sync"]           # shell commands run in a new worktree before launch

[executor]                    # optional
command = ["claude"]          # the executable (resolved on PATH)
args = []                     # extra arguments, e.g. ["--model", "opus"]
```

Repo names may not contain `__` or slashes (report names are `<repo>__<item>.md`).
`worktree_root` must lie outside the control folder and every repo; worktrees live at
`<worktree_root>/<repo>/<NNN_name>`. Unknown keys in a repo table are an error.
A request that changes `conductor.toml` reloads it; an invalid one is logged red and
the previous config stays in force.

## Queue items

`queue/<repo>/NNN_name.md`: YAML front-matter, then the prompt body.

```
---
repo: sandbox            # required; must match the queue folder
locks: [shared]          # named locks beyond the repo's own implicit lock
depends_on: [001_hello]  # 001_hello, 001, other-repo/001_x; each must have finished `done`
after_red: stop          # stop (default) | continue
needs_planner: false     # true: the repo's next item waits until the read mark passes this finish
readonly: false          # true: conductor checks the repo is unchanged afterwards
max_hours: 1             # hard-kill deadline; default from conductor.toml
paths: ['src/ui/**']     # globs the item may change; enables concurrency (see `paths:`)
stage: false             # override the repo's `stage` setting for this item
---
Prompt body.
```

Unknown fields, a mismatched `repo`, bad values or an empty body make the item
**invalid**: logged once (and again whenever the file changes) and its queue holds
until the file is fixed — an item not yet started may be rewritten freely.

An unquoted `depends_on: 001` is the YAML integer 1; conductor matches bare numbers
numerically, so it still resolves.

**Scheduling.** Each repo starts its items strictly in NNN order; the head of a queue
that cannot run holds everything behind it. Different repos run in parallel. The head
runs when all of these hold:

- no item of that repo is running or waiting to merge (the implicit repo lock), unless
  every such item may share the repo with it (see `paths:`);
- the repo is not held after a red (below) and no `needs_planner` report is unread;
- every named lock is declared in `conductor.toml` and free;
- every `depends_on` item finished with status `done` (a dependency still queued
  or running elsewhere means *wait*; one that ended `partial`/`red`, is unknown,
  or sits later in the same queue means *hold*);
- the item id has not run before (a re-run is a new item number).

**Holds** are logged as `hold` events (once per reason change). Waiting on a busy lock
or a running dependency is not a hold. A finished item waiting to merge keeps the repo
lock but releases its named locks.

- *after a red*: when an item ends red and its `after_red` is `stop`, its repo's queue
  holds until a `clear: <repo>` signal. `continue` items don't hold.
- *needs_planner*: the repo's next item holds until the read mark is at or past the
  finished item's `finish` event id.

Items move `queue → running → done` by atomic rename.

## Worktrees and merges

Unless its repo sets `worktrees = false`, each item runs in its own git worktree,
`<worktree_root>/<repo>/<NNN_name>`, on a branch `conductor/<NNN_name>` cut from `main`
at launch. Executors never touch each other's files or Matt's checkout. The preamble
tells the executor to commit to its branch, leave nothing uncommitted, and never touch
`main` or the main checkout.

- **Untracked state.** A worktree holds only tracked files. Each `shared` folder of the
  repo is linked into the worktree at the same path (a directory junction on Windows,
  a symlink elsewhere), so data, a venv or build output is shared, not copied; a change
  there is a change everywhere. A shared folder should be git-ignored in the repo —
  conductor logs a `warning` if it is not, since an executor could commit it. Then the
  `setup` commands run in the worktree through the shell, output to
  `reports/<repo>__NNN_name.setup.log`; a failing or timed-out command is a launch
  failure (red). Setup runs inside the daemon's loop, so a slow one delays everything
  else for its duration. Links are always removed before a worktree is deleted, so
  removing a worktree never reaches into a shared folder.
- **Merging.** When an item finishes `done` or `partial`, it waits in `running/` (phase
  `merge`) and is merged serially per repo, in finish order: rebase the branch onto
  current `main` inside its worktree, fast-forward the main checkout, remove the
  worktree and the branch (`merge`, `worktree` events), then log `finish`. So
  `finish` means merged, and a `depends_on` item never starts before its dependency's
  changes are on `main`.
  - *Rebase conflict:* the rebase is aborted, a `conflict` event names the files, the
    item ends red; branch and worktree are kept, `main` is untouched. A rebase that
    fails with no conflicted file (a stale lock, an untracked file in the way, a file
    held open) is a hold and is retried each tick.
  - *Left its branch:* an executor whose worktree HEAD is no longer on
    `conductor/<NNN_name>` (another branch, detached) ends red, worktree kept — its
    commits are not on the branch conductor would merge.
  - *Main checkout not clean* (tracked changes) or not on `main`: the merge waits,
    logged once as a `hold` (with `merge: true`), and proceeds once it is. Merges
    behind it in the same repo wait too. Untracked files never hold a merge unless the
    fast-forward would overwrite one.
  - *Uncommitted changes* left in the worktree make the item red (they would be lost).
  - *Red item:* never merged; branch and worktree kept for inspection (links removed).
  - *readonly item:* never merged; its worktree and branch are discarded.

  To remove a kept worktree, delete its folder, then `git worktree prune` and
  `git branch -D conductor/<NNN_name>`. Never `git worktree remove --force` a tree
  that still holds a junction: Git for Windows follows it and empties the target
  (proven on this box). Conductor itself never calls it: it unlinks every directory
  link in the tree (whatever `shared` says now), deletes the files, then prunes.

  A `conductor.toml` request that drops a repo with items in `running/` is refused
  (`invalid`); the previous config stays in force.

## `paths:` concurrency

An item may declare `paths:` — globs relative to the repo root, `/`-separated. `*`, `?`
and `[...]` match within a segment, `**` any number of segments, and a glob also covers
everything under a folder it matches (`docs` = `docs/**`); case is ignored.

- **Running together.** A queue head may start while other items of its repo run (or
  wait to merge) only if the repo uses worktrees, both items declare `paths`, every
  pair of their globs is disjoint, and they share no named lock. Otherwise the repo
  lock is exclusive. Disjointness is conservative: two globs count as disjoint only
  when, segment by segment, two literal segments differ or a literal fails a wildcard;
  two wildcard segments, a `**`, or one glob being a prefix of the other count as
  overlapping.
- **Enforcement.** After the run, every file the branch changed (`git diff` from the
  cut point) must match a declared glob. Anything else is a `paths_violation` event
  and the item ends red, unmerged. Enforced in `worktrees = false` repos too.

## Staging

When a repo's queue head is blocked **only** by a busy lock (the repo's or a named one)
or by a dependency that is still running, and the repo uses worktrees with `stage` on
(an item's `stage:` overrides), conductor stages it: it creates the item's worktree and
launches a session with a stage preamble — read the prompt and what it points at, run
read-only commands only, nothing long or heavy, no edits, no asks (no `--mcp-config`),
write a plan as the final message, stop. The item stays in `queue/`; its record is
`running/<repo>/NNN_name.stage.json` (`stage` event, then `staged` with the plan's
first 500 characters). At most one staged item per repo.

When the head can run, conductor checks the worktree is still exactly as cut (HEAD,
tracked files, and no untracked files beyond what setup made), moves it to current
`main` (`git reset --hard`), reruns setup, and resumes the **same session in the same
worktree** with `claude -p --resume <session id>`. The resume message says the lock
is held, how many commits landed since staging (`git log <old>..HEAD`), and then gives
the full execution preamble. The `start` event carries `resumed: true` and `landed`.

A stage is discarded — `warning` event, worktree removed, the item launches fresh
later — if it changed its worktree, left no result or errored, ran past
`stage_minutes`, tampered with the control folder, or the item file changed or left
the queue after staging. The head waits for a stage still running when it could
otherwise start.

**Resume and working directory.** Claude Code stores sessions per working directory
(`~/.claude/projects/<cwd>/<session>.jsonl`). In 2.1.301 `--resume <id>` also found a
session started in a different directory, but conductor resumes in the stage's own
worktree path anyway, so it never depends on that.

## Executors

Each item runs as

```
claude -p --dangerously-skip-permissions --output-format stream-json --verbose --session-id <uuid>
       --mcp-config reports/<repo>__NNN_name.mcp.json
```

with cwd = the item's worktree (the repo itself when `worktrees = false`) and the
prompt on stdin: a conductor preamble followed by the item body. A staged item is
resumed with `--resume <session id>` instead of `--session-id` (see *Staging*). The
preamble names the item and says the session is unattended; says where it works (the
worktree, its branch and cut point, shared folders, the commit rule) and any declared
`paths`; gives the asking
rule (ask only when a wrong guess would waste real work or be hard to undo, give a
default whenever one exists, block only when every default is worse than waiting;
everything else is a branch); the time limit and kill time; the **absolute report
path** (the only file the executor may write outside its repo); the report header and
what each status means; and that the final message ends `Report written to <path>`.
The exact text sent is kept as `reports/<repo>__NNN_name.prompt.md`.

The `--mcp-config` names one stdio server, `conductor`, run by the daemon's own Python
(`python -m conductor.ask_server --control … --item … --default-minutes … --session-id …`);
its one tool is `ask` (see *Questions*).

**Long tool calls.** Claude Code (2.1.301) aborts an MCP tool call that sends no
response or progress for 1800s ("sent no response or progress for 1800s; aborting"),
and `MCP_TOOL_TIMEOUT` does not change that. Two independent guards keep a blocking ask
alive, each proven alone with a 35-minute call: the server entry carries
`"timeout": 604800000` (7 days in ms), and the ask server sends
`notifications/progress` every 60s against the `progressToken` Claude Code puts in
every call's `_meta`.

Conductor generates the session id and passes it with `--session-id`, so the id is
known (in `.run.json` and the `start` event) before the executor writes a byte; phase 3
resumes sessions with it. Variables that bind a process to an enclosing Claude Code
session (`CLAUDECODE`, `CLAUDE_CODE_SESSION_ID`, …) are stripped from the executor's
environment, so conductor can be started from inside a Claude Code terminal.

stdout (stream-json, one JSON object per line, ending with a `"type": "result"` line)
goes to `reports/<repo>__NNN_name.log`; stderr to `.stderr.log`. Stream-json rather
than plain JSON, so a killed run still leaves its transcript.

**Report header.** The report starts with:

```
status: done | partial | red
blocks: <what this blocks, or none>
carry: <what state.md should change, or none>
```

(Bare lines or inside a `---` block; `**status**:` is tolerated.)

**Outcome.** The item's final status is the report's status unless something makes it
red: no report or a bad header, no `result` line in the log, `is_error` in the result,
killed at `max_hours`, interrupted (restart rule), control-folder tamper, a readonly
item that changed its repo, a launch failure (worktree or setup included), a
`paths` violation, uncommitted changes left in the worktree, or a rebase conflict.
The reasons are listed in the `finish` and `red` events.

**max_hours** is enforced by the daemon: past the deadline (stored in `.run.json`, so
it survives restarts) the executor's whole process tree is killed (`kill` event). Time
blocked on a question does not count: no kill while a blocking ask is open, and when it
resolves the deadline moves later by the time it was open (`deadline` in the `answer`
event; `blocked_seconds` in `.run.json`). Time waiting on a defaulted ask does count.

**readonly** items: conductor records the HEAD and tracked-file status of the item's
worktree (or repo) before and compares after; any difference is red. Commits made by
any item are logged as `commit` events (`scope` = the repo, `branch` when in a
worktree) when it exits; the merge then lands them on `main` rebased, under new shas.

## Questions

An executor reaches Matt with the `ask` tool (`mcp__conductor__ask`):

```
ask(question, options?, default?, block?)     # block defaults to false
```

A call writes `questions/<repo>__<NNN_name>__<n>.md` (`n` counts from 1 per item) and
holds the tool call open until the question resolves:

```
---
item: sandbox/012_ask_block
asked: '2026-10-15T14:31:31-07:00'
block: false
default: notes/default-taken.md
deadline: '2026-10-15T14:41:31-07:00'   # asked + ask_default_minutes; null when blocking
session_id: a6874e17-…
---
What should the notes file be called?

Options:
- notes/default-taken.md
- notes/answered.md
```

- **Answer.** The planner (or `conductor answer <id> <text>`) writes
  `questions/<id>.answer.md`: the answer as plain text (front-matter, if any, is
  dropped; a `by:` field in it — the app writes `by: app` — goes on the `answer` or
  `late` event). It is read once it has been unchanged for 1 second. The tool returns
  `Answer to <id>:` and the text.
- **Default.** With a default and `block: false`, an unanswered question returns the
  default at its `deadline`; the tool result says nobody answered and the default was
  taken. An answer file written after that is logged `late` and does nothing else.
- **Block.** `block: true`, or no default at all: the call waits until answered,
  however long. That time does not count toward `max_hours`.
- **Closed.** An item that ends with a question still open (killed, crashed) has it
  closed by the daemon (`question_closed`).

The ask server resolves the question by creating `<id>.result.json` exclusively
(`outcome`: `answered` | `default` | `cancelled`, `text`, `at`, `by`); the daemon only
creates one for `closed`. So a question resolves exactly once, and an executor gets its
answer even while the daemon is down. The daemon observes the folder each tick and logs
`ask`, `answer`, `default`, `late` and `question_closed`, each naming the question
in `detail.question`. Open questions lead the inbox under **Questions** whatever the
read mark says, each with the answer file to write. `conductor questions` lists them;
`conductor answer` takes the full id or an unambiguous short form (`012_ask_block__1`,
`012__1`).

## Toasts

The daemon raises a Windows toast (library: `windows-toasts`, WinRT in-process) for
`ask`, `red`, `tamper`, `hold` and `queue_empty` events. Each title names the project and
the item; the toast's tag is `<project>#<event id>`, which is what the Action Center
history records. Toasts use Command Prompt's registered app id, with "Conductor" as
the attribution line, so nothing has to be installed. A toast that fails is logged as a
`warning` and never stops the daemon. `toasts = false` in `[defaults]` turns them off;
on other platforms each would fail, so turn them off there.

`queue_empty` is logged when an item finishes and its repo has nothing queued or
running.

## conductor init

```
conductor init C:\Code\<project>-control --project <project> --repo app=C:\Code\app [--repo …]
```

creates the folder with the layout above: `conductor.toml` (the project, the repos —
relative like `../app` when beside or under the control folder, else absolute —, `max_hours = 2`,
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
  (`concurrent`) and each ends red, since conductor cannot tell which did it.
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
control folder, plus `request.md` — **written last**:

```
---
targets: [state.md, docs/plan.md]
message: Optional one-line commit message
---
Commit message (used when `message:` is absent; first line is the subject).
```

Conductor waits until nothing in the folder has changed for 2 seconds, checks the
guard, copies the files in (UTF-8 text is written with LF endings; anything else byte
for byte), stages and commits exactly the targets, logs `request` and `commit`, removes
the request folder and re-snapshots. Every file in the folder must be a target and
every target must be present; targets must be relative, inside the folder and outside
the runtime paths. A bad request is logged `request_failed` (red) and renamed
`requests/<stamp>.failed/`. A request that changes nothing is logged with
`no_change` and removed.

## Signals: read, clear, approve, cancel, reorder, reviewed, stop

Every change to the queue that is not a new item goes through `signals/`, so the daemon
carries it out between launches and logs it. The planner (or the app) writes a small
file (any name; `*.tmp` and `*.bad` are skipped) with `key: value` lines, applied in
order:

```
by: planner                  # optional: every event this file causes carries detail `by`
read: 42                     # inbox read through event #42 (`latest` = everything so far)
clear: sandbox               # release the sandbox queue after a red
approve: 007                 # proposed/007_x.md → queue/<its repo>/007_x.md
cancel: sandbox/004          # a queued item (or a proposed one: `cancel: 007`)
reorder: sandbox 006 004     # the listed items swap numbers; unlisted ones stay
reviewed: sandbox/003        # the planner has acted on that finished item's report
stop: daemon                 # the daemon exits cleanly, as on Ctrl+C
```

Items are named `NNN_name` or `NNN` (numerically: `7` is `007`). A ` # comment` after a
value is ignored.

- **approve** — the `proposed/` file must parse as a valid item whose `repo:` is
  configured, and `<repo>/NNN_name` must not already be queued, running, staged or done.
  It moves by one rename; `approve` is logged (item `<repo>/NNN_name`, `from`), then
  the usual `enqueue`.
- **cancel** — a name without a repo is looked up in `proposed/` first, then across
  every queue (a name found in two queues is ambiguous: refused). A running item is
  refused: cancel never kills an executor. A staged head has its stage discarded
  first (process tree killed, worktree removed, the usual `warning`). The file is
  moved, not deleted, to `.conductor/cancelled/<YYYYmmdd-HHMMSS>__<repo|proposed>__NNN_name.md`;
  `cancel` is logged with `from` (`queue`/`proposed`) and `kept`. A cancelled proposal's
  event has item `proposed/NNN_name` and the repo its front-matter names (or `proposed`
  when that repo is missing or not configured).
- **reorder** — `reorder: <repo|proposed> <item> ...`: the listed items take the sorted
  numbers they already hold, in the listed order (each number keeps its zero padding;
  the name after it is unchanged). Refused when an item is unknown, listed twice or
  running, when a new name is taken by an unlisted item, when a new queue name is the
  id of an item that ran or is running (`done/`, `running/`: ids are never reused),
  when any item in `queue/` or `proposed/` has a `depends_on` that names a renumbered
  item, when a renumbered queue item would come before an item of its own queue it
  depends on (it would hold forever), or while an earlier reorder is unfinished. A
  renumbered staged item loses its stage first. A renamed queue item is a new id, so
  it is enqueued again. Renames go through temp names (`NNN_name.reorder~<new name>`,
  not `*.md`), journalled in `.conductor/reorder.json`. A rename that fails (a file
  held open) leaves the journal: a `warning` is logged, every tick rolls it forward
  until it completes (`reorder` logged with `note`), and that queue launches nothing
  meanwhile. A restart finishes an interrupted reorder the same way.
- **reviewed** — `reviewed: <repo>/<NNN_name>` (or `NNN_name`, `NNN`; a name without a
  repo is looked up across every repo, and one found in two is ambiguous: refused).
  It marks one finished item's report as reviewed and logs `reviewed` (item, `finish`:
  the finish event's id, `status`, `by`: the file's `by:` or `unknown`). Refused for an
  item that has not finished (queued, staged or running) or is unknown. An item already
  reviewed is a no-op: a `warning` names the earlier `reviewed` event and the file is
  not rejected. Reviewed state is derived from the event log (an item is reviewed once a
  `reviewed` event names it), so it survives restarts, and it is independent of the
  read mark: reading the inbox never marks anything reviewed, and `needs_planner` still
  waits on the read mark. Several `reviewed:` lines may share a file.
- **stop: daemon** — the daemon finishes the signals of that tick, logs `daemon_stop`
  (`reason: signal`), releases its lock and exits. Executors and stages keep running;
  the next start adopts them.

Conductor processes a file about a second after it stops changing and deletes it. The
whole file is syntax-checked first: an unknown key or malformed line rejects it with
nothing applied. Each verb then checks what it acts on before changing anything; a
verb that fails there stops the file, and the lines before it stay applied (the
`invalid` event lists them as `applied`). A rejected file is logged `invalid` and
renamed `<name>.bad`. While a file's lines apply, `.conductor/signal.json` records it
and the lines done so far; if the daemon dies before the file is gone, the next start
sets the file aside as `.bad` and logs a `warning` naming what applied, so no line
ever applies twice (re-send what is still wanted). The read mark only moves forward. Use the id printed at the top
of `inbox.md` so events that arrived after reading stay unread.

Files are applied in name order. The app writes
`signals/app-<YYYYmmdd-HHMMSS>-<nnnnnn>.tmp` (a zero-padded counter, so clicks in one
second keep their order), then renames it to `.signal`, always with `by: app`.

## Inbox

`inbox.md` is regenerated whenever the log or the read mark changes (and every 30s
for the *Now* section). First **Questions**: every open ask, regardless of the read
mark. Then **Unreviewed reports**: every finished item without a `reviewed` event,
oldest first, with its status, finish time, finish event id, report path and `carry:`
line; it shows on every inbox, read mark or not, until a `reviewed:` signal names the
item. Then every event after the read mark, grouped **Red** (tamper first,
then `red`, `conflict`, `paths_violation`, `kill`, `request_failed`, `invalid`),
**Holds**, **Finished** (status, blocks, carry, report path), **Questions resolved**
(answers, defaults taken, late answers, closes), **Requests and commits** (merges
included), **Other**; then **Now**: per repo, what is running (or merging), staged
(`staging`/`staged`), queued, waiting or held, plus proposed items. A hold stays in
the Holds section until the item starts, merges, finishes or is cancelled, or a
reorder renames it (its old id is gone; the renamed item gets its own hold). `enqueue`, `start`,
`stage` and `worktree` events are left out of the inbox (they are in
`conductor events`). `conductor status` lists the same per repo: running (pid, branch,
kill time), merging (with the reason it waits), staging/staged, queued.
`conductor inbox` prints the same thing without moving the mark.

## The app

`uv run conductor app [<control folder> …]` (or the `conductor-app` gui script, no
console) opens a PySide6 window with one tab per control folder. Folders named on the
command line are added; the list is remembered across launches (QSettings, org
`conductor`, app `conductor-app`, key `controls`) and edited with *Add project…* /
*Remove project*. A remembered folder that fails to load (drive not mounted,
`conductor.toml` broken) is reported in the status bar and stays remembered. Qt is
imported only by this command; the daemon and CLI never need it.
Code: `src/conductor/app/` — `model.py` and `actions.py` have no Qt (they reuse
`state.scan`, `state.decide`, `questions`, `config`); `panels.py` and `window.py` are
the widgets.

**What it reads and writes.** It opens `events.db` with a `file:…?mode=ro` URI, so it
can neither create nor write it; a missing `events.db` reads as an empty log and stays
missing. (Opening a WAL database read-only while no daemon has it open leaves an empty
`events.db-wal` and an `events.db-shm` beside it — SQLite's, harmless, git-ignored.)
The URI keeps a mapped drive as is and puts a UNC server in the path
(`file:////server/share/…`), which SQLite accepts. Every 1.5s it re-reads only events
with `id >` the last one seen, and rescans the control folder; a read that fails for a
moment (database busy) counts as no change, and only a log with fewer events than seen
(replaced) resets the Events table. New events reach the table before anything else
refreshes, and each panel refreshes on its own, so one bad file cannot freeze the rest.
On Windows its reads can briefly block the daemon's renames; the daemon retries those
(`util.retry_io`, up to 3s). It never moves a queue file. Its only writes are signal files, answer
files, its own settings, and starting the daemon.

**Header.** The project's state, whether its daemon is alive (`.conductor/daemon.json`,
pid + creation time), and a count of the app's signals still waiting in `signals/` (or
rejected as `.bad`). *Start daemon* spawns `<python> -m conductor run <folder>` (the
console `python.exe` even when the app runs under `pythonw.exe`; git calls pass
`CREATE_NO_WINDOW` too, so nothing flashes a console window)
detached (no window, its own process group, out of the caller's job object when
allowed), output appended unbuffered (`python -u`) to `.conductor/daemon.out.log`; *Stop daemon…* asks first,
then writes a `stop: daemon` signal. Executors and stages keep running either way.

**Panels.**

- *Repos* — per repo: the running item with its phase (`running`, `merging`,
  `concluding`) and elapsed time, the queue in order with the head's decision (`wait`,
  `hold`, …) and any invalid item, and a hold row, listed first under its repo (the head
  item then reads *blocked by hold*): a red hold has a **Clear hold** button
  (`clear: <repo>`); each queued item has **Cancel** (asks first;
  `cancel: <repo>/<NNN_name>`). A staged item is the queue head, so it is one row whose
  state carries both: `staging` (with elapsed) or `staged (plan ready)`, then `· head`
  or `· blocked by hold`.
  Under the queue, a *finished* row heads the repo's finished items, newest first: the
  10 most recent plus every unreviewed one however old, each with its status, finish
  time, carry and a *reviewed* / *unreviewed* mark (unreviewed bold, coloured by
  status; reviewed muted). Double-click opens the trail, as for live items. Each
  unreviewed row has **Mark reviewed** (`reviewed: <repo>/<NNN_name>`, `by: app`; the
  button disables once clicked and the row turns *reviewed* when the daemon logs it).
  The tab label reads *Repos (N unreviewed)*, in amber, while any report is unreviewed.
- *Questions* — open asks, blocking ones first, then by soonest default; each shows
  *BLOCKING* or the default and a live countdown to it, its options as buttons that
  fill the answer box (the default's marked *(default)*), and **Answer**, which writes
  `questions/<id>.answer.md` through the same function as `conductor answer` (never
  overwriting one), with `by: app` front-matter so the `answer` event names the app. An answer to a question that resolved meanwhile (its default taken)
  is reported as late, as the CLI does. Resolved questions are listed beneath, newest
  first. Question files are re-parsed only when they or their result change.
- *Events* — by default the **Activity** view: workflow-level lines only, worded
  plainly — `prompt X started`, `report X written — done/partial/red (carry: …)`,
  `report X reviewed`, questions asked / answered / default taken (and late answers and
  closes), approved, cancelled, reordered, cleared, `doc update committed: <message>`
  (a `request`) or `request failed`, daemon started / stopped, and every red, `warning`
  or `invalid` event whatever its kind. A `finish` is the *report written* line, not a
  separate entry. Everything else (`worktree`, `stage`, `staged`, `merge`, `commit`,
  `hold`, `enqueue`, `queue_empty`, `read`, …) appears only with **Advanced** checked
  (off by default), which also shows each event in the log's own wording. The *Kind*
  list offers only the kinds the current mode shows. In both modes: the whole log, filterable by repo, item (`001`, `001_x`, `repo/001_x`, or
  any substring of the id), kind, a from/to time range and *reds only*. Selecting an
  event shows its item's trail beside it: every event of the item (the selected one
  expanded with its full detail; wrapped lines hang under the event's text, or under the
  detail indent when the pane is too narrow to leave ~20 characters there), the prompt as sent (`.prompt.md`, else the item
  file), the report, the executor log and the stage log rendered from stream-json (text,
  `→ tool: input`, `← result`, the final result line with turns, time and cost), and
  *Git*: the item's commits with diffs from `conductor/<NNN_name>` while that branch
  exists, else the last `commits` commits ending at the `merge` event's `sha` (the
  rebased commits on main), else (an item run without a worktree) `git show` of the
  shas in its `commit` events. Git runs on a worker thread, only when its tab is shown,
  and again only when the item has new events. Moving to another event of the same
  item only refocuses the Events tab; new events for the open item update each tab in
  place (scroll position kept; a log being read at its end follows it). Rendered logs
  are cached per file version. A cancelled item's prompt comes from its kept copy in
  `.conductor/cancelled/` (the `cancel` event's `kept`). Keys: ↑/↓, PgUp/PgDn,
  Home/End move (the trail follows after a 120ms pause); Ctrl+F item filter (Enter
  returns to the list); Ctrl+R reds only; Ctrl+L the list; Alt+1…6 the trail tabs.
  Double-clicking an item in any other panel opens its trail here.
- *Proposed* — `proposed/*.md` with its repo, first line and any problem (bad
  front-matter, unknown repo); **Approve** (`approve: NNN_name`; enabled only for a
  selected item without problems), **Cancel** (asks
  first), **Move up / Move down** (`reorder: proposed <a> <b>` swapping the two
  neighbours' numbers; the selection follows the moved item to its new name, and a
  second move waits until the first has landed).

The *Questions* and *Proposed* tab labels carry their counts and are coloured from any
panel: red with `N blocking` while a question blocks, amber while only defaults are
pending or items await approval. The app always draws light (Fusion style and an
explicit palette, so a dark Windows theme cannot flip it); its colours are named tokens
in `app/theme.py`, each text colour at least 4.5:1 on the backgrounds it is drawn on.

Every action is a signal file `signals/app-<YYYYmmdd-HHMMSS>-<nnnnnn>.signal` (written as
`.tmp`, then renamed) starting `by: app`, so the daemon carries it out, logs it, and
nothing races a launch. With the daemon down, actions wait in `signals/`.

**Tray.** A coloured disc drawn at runtime shows the most urgent state across all
projects: red (a repo held after a red, or a project's daemon down while it has items
queued or running) > asking (an open question) > running (an item running or staged)
> idle (grey). Its tooltip lists each project's state; clicking opens the window.
Closing the window hides it to the tray; *Quit* in the tray menu exits. Neither touches
a daemon or an executor. Without a system tray, closing the window quits.

**Toast click.** Not wired. Toasts are raised under Command Prompt's app id, so a plain
click is delivered to that app id, never to this app. `windows-toasts` can
make a toast *protocol*-activated (`Toast(launch_action="scheme:…")`), which Windows
honours for unpackaged apps; opening the app on an item would need a URI scheme
(e.g. `conductor-app:`) registered under `HKCU\Software\Classes` pointing at
`conductor-app`, plus a single-instance hand-off to a running app. Unverified and left
for later; the toast's tag (`<project>#<event id>`) names the event, and *Events* lists
ids in its first column.

## Event log

`events.db`, table `events(id, ts, kind, item, repo, paths, detail)`. `ts` is local
ISO time with offset; `paths` is a JSON list (control-folder-relative where inside
it); `detail` is a JSON object. Triggers reject UPDATE and DELETE.

| kind | when | notable detail |
|---|---|---|
| `daemon_start` / `daemon_stop` | daemon lifecycle | pid; running items at stop; stop `reason` (`signal`: a `stop: daemon` signal, `interrupt`: Ctrl+C, `error`: three ticks in a row failed on an `OSError`) |
| `enqueue` | an item is first seen in `queue/` | its front-matter fields |
| `invalid` | bad item, signal, stray queue folder, bad config | error |
| `hold` | a queue head cannot run, or a merge waits (reason changed) | reason; merge |
| `start` | executor launched | session_id, pid, deadline, worktree, branch; resumed, landed (a staged item); error (launch failed) |
| `stage` | a blocked queue head launched read-only | session_id, pid, worktree, deadline |
| `staged` | the stage finished cleanly | session_id, plan (first 500 chars), cost_usd |
| `worktree` | created, moved (a staged worktree to current main), removed (merged), kept (red), discarded | action, branch, base, note |
| `merge` | an item's branch landed on main | branch, sha (main after), commits |
| `conflict` | the rebase onto main conflicted (red) | branch, files, error |
| `paths_violation` | the branch changed files outside `paths` (red) | files, paths_declared |
| `adopt` | restart found the executor (or stage: `stage: true`) alive | pid, session_id |
| `recover` | restart found it finished while the daemon was down, or a merge to resume | note |
| `requeue` | restart found it never launched | reason |
| `kill` | max_hours reached | pid, reason |
| `finish` | item done (after its merge, when it had one) | status, report_status, reasons, blocks, carry, session_id, exit_code, cost_usd, turns, branch, merged |
| `red` | the item's status is red | reasons, after_red |
| `tamper` | control folder changed during an executor's window | changes, concurrent |
| `drift` | control folder changed with no executor running | changes |
| `request` / `request_failed` | a request applied or rejected | request, message, targets / error |
| `commit` | conductor committed the control folder, or an item committed in its repo | scope, sha, subject |
| `read` / `clear` | signals | through / repo |
| `reviewed` | a `reviewed:` signal marked a finished item's report as acted on | finish (its event id), status, by |
| `approve` | a `proposed/` item moved into its queue | item, from |
| `cancel` | a queued or proposed item set aside in `.conductor/cancelled/` | from (`queue`/`proposed`), kept |
| `reorder` | items of a queue (repo) or of `proposed` renumbered | before, after (names in queue order); note (finished after a restart) |
| any signal-caused event | a signal file had `by:` | by |
| `ask` | a question file appeared | question (id), text, block, default, deadline |
| `answer` | answered before it resolved | question, text, waited_seconds, deadline (moved, when blocking); by (the answer file's `by:`) |
| `default` | the default deadline passed unanswered | question, text (the default) |
| `late` | an answer file after a default or close | question, text, outcome; by |
| `question_closed` | resolved with no answer: item finished, or the tool call was cancelled | question, outcome, reason |
| `queue_empty` | an item finished and its repo has nothing queued or running | last |
| `config_reload`, `warning` | config applied; runtime path not ignored; a toast failed; a stage discarded; a shared folder missing or not ignored | note |

## Restart rule

A restart never loses an item and never runs one twice. A tick that dies on an
`OSError` (a file another process holds open, past the 3s retry) is treated as a
restart without letting go of the lock: a `warning` is logged, the daemon drops what it
holds in memory and runs the same recovery; three such ticks in a row stop it
(`daemon_stop`, `reason: error`). For each item left in `running/`:

1. its `finish` event is already logged → only the move to `done/` was lost; finish it;
2. its `.run.json` has no pid and no live process carries its session id → it never
   launched; move it back to `queue/` (`requeue`);
3. its executor is alive (pid + creation time match, or found by its `--session-id` on
   a command line) → **adopt** it: keep waiting, same deadline, same guard snapshot;
4. the executor is gone but its log has a `result` line → finished while conductor was
   down; harvest it normally (`recover`);
5. otherwise → **red**, reason *interrupted*. It is never re-run automatically — it may
   have committed part of its work; a follow-up is a new queue item. Its worktree and
   branch are kept.

A judged item saves its outcome in `.run.json` before anything else happens. Phase
`concluding` (not merging: red, readonly, launch failed — or merged/conflicted and
being cleaned up) is finished from the saved outcome, worktree cleanup repeated. A
record with no pid but an existing log was spawned after all; it is judged, never
requeued. `red` is logged before `finish`, so a red hold survives any crash.
Phase `merge` (the executor exited and was judged; the
merge had not concluded) is taken up again (`recover`): the merge is repeated from the
top, which is safe at any point — a rebase left in progress is aborted first, and a
branch already contained in `main` (the fast-forward happened) or already deleted
counts as merged. A `.run.json` without its item (the launch died before the move) is
dropped and its fresh worktree discarded; a requeued item's worktree is discarded too.

Stages (`.stage.json`) are not executions, so nothing about them can run an item
twice:

- phase `staged` → kept; the item resumes when it can run;
- the stage process is alive → adopted (`adopt`, `stage: true`);
- it exited and its stage log has a `result` → judged as if just exited (`staged`, or
  discarded if it changed its worktree);
- otherwise (never launched, or died without a result) → discarded with a `warning`;
  the item launches fresh;
- the item already has a run record → the stage record is dropped (the resume launch
  got that far; the run owns the worktree).

The launch order makes this sound: write `.run.json`, create the worktree, move the
item to `running/`, drop the stage record (if resuming), spawn, record the pid; a
`finish` event is logged before the item moves to `done/`.

## Platform notes

Windows-first, cross-platform by construction. Everything platform-specific is in
`src/conductor/procs.py`: executors start with `CREATE_NEW_PROCESS_GROUP |
CREATE_NO_WINDOW` on Windows (`start_new_session` on POSIX) so Ctrl+C or closing the
daemon's console doesn't reach them; processes are identified by pid **and** creation
time (pids are reused); kills take the whole tree (psutil). All files conductor writes
are UTF-8 with LF endings.

Windows still records the daemon as the executors' parent, so `taskkill /T` on the
daemon kills them too. To stop only the daemon use Ctrl+C or `taskkill /F /PID <pid>`
without `/T`.

## Development

`uv run pytest` runs the suite against a fake executor (`tests/fake_claude.py`, which
obeys `FAKE …` lines in the prompt), so the scheduling, guard, request, restart,
question, worktree, merge and staging paths are covered without spending Claude runs.
A staging prompt makes the fake obey only `FAKE stage-…` lines and remember the prompt
under its session id; `--resume` recalls it, so a test can see that a resumed session
kept what it read while staging. `FAKE ask {json}` calls the
real ask server through the item's `--mcp-config` using `tests/mcp_client.py`, a
scripted MCP client that also runs standalone:
`python tests/mcp_client.py <mcp.json> '{"question": "…", "default": "…"}'`. The sandbox
(`C:\Code\conductor-sandbox`, `C:\Code\conductor-sandbox-control`) is for end-to-end
runs with real executors.
