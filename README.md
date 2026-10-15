# claude-conductor

Conductor is a daemon that runs queued Claude Code prompts for one project. It is
pointed at the project's **control folder**, runs approved prompts from per-repo
queues as headless `claude -p` sessions (one at a time per repo), and records
everything in an append-only event log from which the inbox and every other view is
derived. `matt-controller-workflow.md` is how a project uses it; this file is how it
works and is the canon for commands, file formats, fields and event kinds.

## Install and run

```
uv sync
uv run conductor run    <control folder>   # the daemon, foreground; Ctrl+C stops it
uv run conductor status [<control folder>]
uv run conductor inbox  [<control folder>]
uv run conductor events [<control folder>] [--since X] [--item Y] [--repo R] [--kind K] [--paths]
```

The control folder argument defaults to `$CONDUCTOR_CONTROL`, then the current folder.
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
running/<repo>/NNN_name.md     the item while it runs, plus NNN_name.run.json
done/<repo>/NNN_name.md        finished items, plus their .run.json
reports/<repo>__NNN_name.md    the executor's report
reports/<repo>__NNN_name.log   raw executor output (stream-json); also .stderr.log, .prompt.md
requests/<stamp>/              planner → conductor: replacement files + request.md
signals/                       planner → conductor: read mark, queue clears
questions/                     reserved (phase 2)
inbox.md                       generated digest since the read mark
events.db                      append-only event log (SQLite, WAL)
.conductor/                    daemon lock and the guard snapshot
```

Conductor creates the runtime folders on start and logs a `warning` event if any of
them is not git-ignored. A suitable `.gitignore`:

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

[defaults]
max_hours = 2                 # per item unless the item sets max_hours
poll_seconds = 2              # daemon loop interval

[repos]                       # name = path; relative paths resolve from the control folder
sandbox = "../conductor-sandbox"

[executor]                    # optional
command = ["claude"]          # the executable (resolved on PATH)
args = []                     # extra arguments, e.g. ["--model", "opus"]
```

Repo names may not contain `__` or slashes (report names are `<repo>__<item>.md`).
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
---
Prompt body.
```

Unknown fields, a mismatched `repo`, bad values or an empty body make the item
**invalid**: logged once (and again whenever the file changes) and its queue holds
until the file is fixed — an item not yet started may be rewritten freely.

An unquoted `depends_on: 001` is the YAML integer 1; conductor matches bare numbers
numerically, so it still resolves.

**Scheduling.** Each repo runs its items strictly in NNN order, one at a time; the head
of a queue that cannot run holds everything behind it. Different repos run in
parallel. The head runs when all of these hold:

- no item of that repo is running (the implicit repo lock);
- the repo is not held after a red (below) and no `needs_planner` report is unread;
- every named lock is declared in `conductor.toml` and free;
- every `depends_on` item finished with status `done` (a dependency still queued
  or running elsewhere means *wait*; one that ended `partial`/`red`, is unknown,
  or sits later in the same queue means *hold*);
- the item id has not run before (a re-run is a new item number).

**Holds** are logged as `hold` events (once per reason change). Waiting on a busy lock
or a running dependency is not a hold.

- *after a red*: when an item ends red and its `after_red` is `stop`, its repo's queue
  holds until a `clear: <repo>` signal. `continue` items don't hold.
- *needs_planner*: the repo's next item holds until the read mark is at or past the
  finished item's `finish` event id.

Items move `queue → running → done` by atomic rename.

## Executors

Each item runs as

```
claude -p --dangerously-skip-permissions --output-format stream-json --verbose --session-id <uuid>
```

with cwd = the repo and the prompt on stdin: a conductor preamble followed by the item
body. The preamble names the item, says the session is unattended and never stops to
ask (every gate is a branch), gives the time limit and kill time, the **absolute report
path** (the only file the executor may write outside its repo), and the report header.
The exact text sent is kept as `reports/<repo>__NNN_name.prompt.md`.

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
item that changed its repo, or a launch failure. The reasons are listed in the
`finish` and `red` events.

**max_hours** is enforced by the daemon: past the deadline (stored in `.run.json`, so
it survives restarts) the executor's whole process tree is killed (`kill` event).

**readonly** items: conductor records the repo's HEAD and tracked-file status before
and compares after; any difference is red. Commits made by any item are logged as
`commit` events (`scope` = the repo).

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

## Signals: read mark and queue clears

The planner writes a small file (any name) into `signals/` with `key: value` lines:

```
read: 42        # inbox read through event #42 (`latest` = everything so far)
clear: sandbox  # release the sandbox queue after a red
```

Conductor processes it about a second after it stops changing, logs `read` / `clear`
and deletes the file. A bad signal is logged `invalid` and renamed `<name>.bad`. The
read mark only moves forward. Use the id printed at the top of `inbox.md` so events
that arrived after reading stay unread.

## Inbox

`inbox.md` is regenerated whenever the log or the read mark changes (and every 30s
for the *Now* section): every event after the read mark, grouped **Red** (tamper first,
then `red`, `kill`, `request_failed`, `invalid`), **Holds**, **Finished** (status,
blocks, carry, report path), **Requests and commits**, **Other**; then **Now**: per
repo, what is running, queued, waiting or held, plus proposed items. `enqueue` and
`start` events are left out of the inbox (they are in `conductor events`).
`conductor inbox` prints the same thing without moving the mark.

## Event log

`events.db`, table `events(id, ts, kind, item, repo, paths, detail)`. `ts` is local
ISO time with offset; `paths` is a JSON list (control-folder-relative where inside
it); `detail` is a JSON object. Triggers reject UPDATE and DELETE.

| kind | when | notable detail |
|---|---|---|
| `daemon_start` / `daemon_stop` | daemon lifecycle | pid; running items at stop |
| `enqueue` | an item is first seen in `queue/` | its front-matter fields |
| `invalid` | bad item, signal, stray queue folder, bad config | error |
| `hold` | a queue head cannot run (reason changed) | reason |
| `start` | executor launched | session_id, pid, deadline |
| `adopt` | restart found the executor alive | pid, session_id |
| `recover` | restart found it finished while the daemon was down | |
| `requeue` | restart found it never launched | reason |
| `kill` | max_hours reached | pid, reason |
| `finish` | item done | status, report_status, reasons, blocks, carry, session_id, exit_code, cost_usd, turns |
| `red` | the item's status is red | reasons, after_red |
| `tamper` | control folder changed during an executor's window | changes, concurrent |
| `drift` | control folder changed with no executor running | changes |
| `request` / `request_failed` | a request applied or rejected | request, message, targets / error |
| `commit` | conductor committed the control folder, or an item committed in its repo | scope, sha, subject |
| `read` / `clear` | signals | through / repo |
| `config_reload`, `warning` | config applied; runtime path not ignored | |

## Restart rule

A restart never loses an item and never runs one twice. For each item left in
`running/`:

1. its `finish` event is already logged → only the move to `done/` was lost; finish it;
2. its `.run.json` has no pid and no live process carries its session id → it never
   launched; move it back to `queue/` (`requeue`);
3. its executor is alive (pid + creation time match, or found by its `--session-id` on
   a command line) → **adopt** it: keep waiting, same deadline, same guard snapshot;
4. the executor is gone but its log has a `result` line → finished while conductor was
   down; harvest it normally (`recover`);
5. otherwise → **red**, reason *interrupted*. It is never re-run automatically — it may
   have committed part of its work; a follow-up is a new queue item.

The launch order makes this sound: write `.run.json`, move the item to `running/`,
spawn, record the pid; a `finish` event is logged before the item moves to `done/`.

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
obeys `FAKE …` lines in the prompt), so the scheduling, guard, request and restart
paths are covered without spending Claude runs. The sandbox
(`C:\Code\conductor-sandbox`, `C:\Code\conductor-sandbox-control`) is for end-to-end
runs with real executors.
