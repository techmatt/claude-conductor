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
uv run conductor audits [<control folder>] [--all]         # unused audit notes per repo (--all: used too)
uv run conductor answer <id> <text…> [--control <folder>]  # writes the answer file
uv run conductor tell <item> <text…> [--control <folder>]  # a message to a running executor
uv run conductor amend <item> <text…|file> [--control <folder>]  # add to an item's instructions (see Amendments)
uv run conductor checkpoint [<label…>] [--control <folder>]   # a new checkpoint (see Checkpoints)
uv run conductor open <item> [--control <folder>]          # a finished item's session, in a new console
uv run conductor run-target <name> [--stop] [--control <folder>]   # start a [run.<name>] target, open its URL
uv run conductor pick <repo>/<manifest path> [--control <folder>]  # open a picker in the browser (see Picks)
uv run conductor init <control folder> --project NAME --repo NAME=PATH [--repo …]
uv run conductor app [<control folder> …]                  # the desktop app (see The app)
```

The control folder argument defaults to `$CONDUCTOR_CONTROL`, then the current folder.
`conductor app` with no folder adds `$CONDUCTOR_CONTROL` if set, else the current folder
only when it holds a `conductor.toml`; it always opens its remembered projects too.
One daemon per control folder: a second `run` refuses while the first is alive
(`.conductor/daemon.json` holds its pid and process creation time).

`--since` takes an event id (`#12` or `12`, exclusive), a relative age (`30m`, `2h`,
`1d`), a clock time today (`5:25pm`), an ISO time, `checkpoint` (from the latest
checkpoint's own event on) or `checkpoint N` (from checkpoint #N on). `--item` takes `sandbox/001_x`,
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
queue/<repo>/NNN_name.amend.md an amendment to that item (`.amend.2.md`, … after it); never an item
proposed/NNN_name.md           awaiting approval (shown in status/inbox, never run)
running/<repo>/NNN_name.md     the item while it runs or waits to merge, plus NNN_name.run.json
running/<repo>/NNN_name.stage.json   a staged queue head (the item itself stays in queue/)
done/<repo>/NNN_name.md        finished items, plus their .run.json
reports/<repo>__NNN_name.md    the executor's report
reports/<repo>__NNN_name.log   raw executor output (stream-json); also .stderr.log, .prompt.md,
                               .mcp.json, .settings.json, .setup.log, and .stage.log /
                               .stage.prompt.md for a stage; .audit.md (audit notes);
                               .amend.md (every amendment delivered, in order, with times)
requests/<stamp>/              planner → conductor: replacement files + request.md
signals/                       planner/app → conductor: read, clear, approve, cancel, reorder, reviewed, tell, checkpoint, run, pick, stop
questions/                     executor asks: <id>.md, <id>.answer.md, <id>.result.json
picks/                         Matt's saved picker choices: <name>.choices.json (see Picks)
inbox.md                       generated digest since the read mark
events.db                      append-only event log (SQLite, WAL)
.conductor/                    daemon lock (daemon.json), guard snapshot, cancelled/ (kept
                               cancelled items), reorder.json and signal.json (crash journals),
                               daemon.out.log (a daemon started by the app), progress/ and
                               mail/ (progress lines, messages to executors: see *Progress and messages*)
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
picks/
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
merge_minutes = 10            # a merge executor resolving a prose conflict is killed after this
foreground_seconds = 60       # an executor's foreground shell call moves to the background after this; 0 = off (see Messages)
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
prose = ["CLAUDE.md", "README.md", "docs/**/*.md"]   # the default: exempt from `paths`, merged textually
background = ["npm test*", "*playwright test*"]       # shell commands executors always background (default none; see Messages)

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
ultracode: true          # launch with Claude Code's ultracode on (see Executors, *Ultracode*)
audit: true              # keep audit notes beside the report (see Audits and templates)
template: cleanup        # cleanup | general_audit | test_perf | disk_audit | claude_md_cleanup: a conductor-owned prompt (same section)
---
Prompt body.
```

Unknown fields, a mismatched `repo`, bad values or an empty body (allowed with
`template:`, whose text is the prompt) make the item **invalid**: logged once (and again whenever the file changes) and its queue holds
until the file is fixed — an item not yet started may be rewritten freely.

**Rewriting a queued item.** Conductor parses `queue/` afresh every tick, so a rewrite
of an item that has not launched takes effect: the launch reads the file as it is then.
Each change to a queued file's bytes logs an `edited` event (an Activity event, so the
planner and the Events tab see that the rewrite landed). A staged item's stage belongs
to the text it read, so a rewrite discards it and the item launches fresh (see
*Staging*). Once the item has moved to `running/`, edits are never read. Changes made
while the daemon is down log no `edited` (the first scan sees only the new text), but a
stage is still discarded by its hash. To add to a plan that still stands, keeping a
stage, or to change a running item, amend it instead (*Amendments*).

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
  there is a change everywhere. A shared folder that does not exist yet in the main
  checkout is created there first (noted on the `worktree` event), so every worktree
  links the same one; a *file* at that path is a `warning` and is not linked. A shared
  folder should be git-ignored in the repo —
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
  - *Prose conflict:* when every conflicted file is prose (the repo's `prose` globs,
    see `paths:`), the rebase is left stopped and a short **merge executor** (headless
    Claude Code, no ask tool, no messages, at most `merge_minutes`) runs in the item's
    worktree, logged `resolve`. Its prompt names the files, says which side of the
    markers is `main` and which is the item, lists the commits that landed on `main`
    and the item's commit and prompt (for intent only), and tells it to keep both
    sides' intent, touch nothing else and run no git command. When it exits, conductor
    checks its work: a result, no conflict marker (`<<<<<<<`/`>>>>>>>` lines) left,
    the stopped rebase where it was, no other tracked or untracked file changed, no
    control-folder change. Then conductor stages the files and continues the rebase
    (`--skip` if the resolution leaves the replayed commit empty); a later stop does
    the same again, and the merge goes on as usual. The `merge` event lists
    `resolved` files. Output: `reports/<repo>__NNN_name.merge.log` (and
    `.merge.prompt.md`, `.merge.stderr.log`). A merge executor that fails any check
    is a *rebase conflict* below: the rebase is aborted, so the branch keeps the
    item's version and `main` its own, and the `conflict` event carries `resolver`
    (its log) and the failed check as `error`. While it runs, later merges in that
    repo wait, and the tamper guard counts it as a running executor. A restart adopts
    it by pid. A conflict touching any non-prose file is never handed to it.
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

  *Leftover worktrees.* Removing a merged, readonly or discarded item's worktree can
  fail while another process holds a file in it (a dev server an executor left
  running, an editor): `PermissionError 13` on Windows. The failure is a `warning` and
  a row in `.conductor/leftovers.json`; the item concludes as usual. Conductor retries
  each row at daemon start and whenever that repo's queue empties (`queue_empty`, but
  not the row of the item that just emptied it, since whatever held its files a
  moment ago still does). A retry that works logs the `worktree` event the first try
  would have (`removed`/`discarded`, note `leftover removed on retry (...)`) and drops
  the row; one that still fails logs a `warning` and keeps it. A row whose path a live
  item has since taken over (a stage discarded, then the item launched fresh there) is
  dropped without touching the path.

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
- **Prose files.** Each repo's `prose` globs (default `["CLAUDE.md", "README.md",
  "docs/**/*.md"]`; `prose = []` turns it off) name the shared prose nearly every item
  touches. Changes to them never count against `paths`: an item may edit them without
  declaring them, and two items that both edit `README.md` still run together when
  their code paths are disjoint. A declared glob that names only prose (a prose glob
  itself, or a literal path one covers, like `README.md` or `docs/a/b.md`) is left out
  of the disjointness check, so an item whose `paths` are all prose shares with
  anything that declared `paths`. A wildcard like `*.md` or a folder like `docs` is
  not prose-only and still counts. At merge, prose merges textually through the
  rebase; a real conflict there goes to a merge executor (*Worktrees and merges*,
  *Prose conflict*). The preamble tells a `paths` item its prose files are exempt.

## Staging

When a repo's queue head is blocked **only** by a busy lock (the repo's or a named one)
or by a dependency that is still running, and the repo uses worktrees with `stage` on
(an item's `stage:` overrides), conductor stages it: it creates the item's worktree and
launches a session with a stage preamble — read the prompt and what it points at, run
read-only commands only, nothing long or heavy, no edits, no asks (no `--mcp-config`),
write a plan as the final message, stop. The item stays in `queue/`; its record is
`running/<repo>/NNN_name.stage.json` (`stage` event, then `staged` with the plan's
first 500 characters). At most one staged item per repo. A `template: cleanup` or
`template: disk_audit` item never stages: what it is handed (audit notes, sizes) is
fixed at launch, and the item it waits on may still change it.

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
       --mcp-config reports/<repo>__NNN_name.mcp.json --settings reports/<repo>__NNN_name.settings.json
```

with cwd = the item's worktree (the repo itself when `worktrees = false`) and the
prompt on stdin: a conductor preamble followed by the item body. A staged item is
resumed with `--resume <session id>` instead of `--session-id` (see *Staging*). The
preamble names the item and says the session is unattended; says where it works (the
worktree, its branch and cut point, shared folders, the commit rule) and any declared
`paths`; gives the asking
rule (ask only when a wrong guess would waste real work or be hard to undo, give a
default whenever one exists, block only when every default is worse than waiting;
everything else is a branch); the progress rule (a `progress` line at the start, with a
first ETA once it can estimate, at real milestones, and when the estimate changes
materially; not chatter); that a message from Matt may arrive after a tool call,
outranks the prompt where they conflict, and is answered through `progress`; the *Short calls* rule (background anything over `foreground_seconds`, poll it in calls no longer, don't idle; see *Progress and messages*); the time limit and kill time; the **absolute report
path** (the only file the executor may write outside its repo); the report header and
what each status means; the *Pickers* paragraph (when to write a picker manifest, its
schema inline, and the `pick:` header line: see *Picks*); and that the final message
ends `Report written to <path>`.
The exact text sent is kept as `reports/<repo>__NNN_name.prompt.md`.

The `--mcp-config` names one stdio server, `conductor`, run by the daemon's own Python
(`python -m conductor.ask_server --control … --item … --default-minutes … --session-id …`);
its tools are `ask` (see *Questions*) and `progress` (see *Progress and messages*). The
`--settings` file holds the PostToolUse message hook and the PreToolUse *Short calls* hook (same section), and
`"ultracode": true` for an `ultracode` item. A stage gets neither file.

**Ultracode.** Claude Code's ultracode is a session setting: once it is on, the session
is told "Ultracode is on" and uses the Workflow tool (multi-agent orchestration) on
every substantive task. In 2.1.302, probed with headless `-p` runs in the sandbox
(Opus 5.5) that quoted their own context:

| route | ultracode on? |
|---|---|
| `"ultracode": true` in the `--settings` file | **yes** |
| `--effort ultracode` | **yes** (also sets effort `xhigh`) |
| the word `ultracode` in the `-p` prompt | no: the keyword triggers only in typed input, never in `-p` |
| a message (`tell:`) saying "use ultracode", delivered by the PostToolUse hook | no: hook context is not the user's prompt |
| the `--settings` file rewritten mid-run to add the key | no: read once at launch |

So conductor uses the first route. Proven through conductor in the sandbox: an
`ultracode: true` item's session carried the "Ultracode is on" notice. Ultracode does
not force a workflow: the notice leaves trivial turns solo, and the executor judges
what counts. A 23-file doc audit (`sandbox/043`) was done solo, without a workflow.
So `ultracode: true` pays off on genuinely large work. An item with `ultracode: true` front-matter
launches (or resumes, after staging) with the key in its `--settings` file; a stage
never has it, nor does a merge executor. Switching an executor *mid-run* has no
working route: a `tell:` saying "use ultracode" arrives as an ordinary message, and the
executor treats it as one. To give running work ultracode, let the item finish (or
cancel it while queued) and queue it, or a follow-up, with `ultracode: true`. A
Workflow call needs no approval in an executor (`--dangerously-skip-permissions`), and
the Workflow tool is present in every `-p` session; only the opt-in differs.

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

**Log compaction.** Run logs are most of a control folder's size (Word Warriors: 144 MB
of 150 MB after 29 items). Nearly all of it is file copies the stream repeats: each
image an executor reads appears twice as base64 (the tool result and
`tool_use_result.file.base64`), and each edit carries the whole file before it
(`tool_use_result.originalFile`). When an item finishes, conductor rewrites its
`.log`, `.stage.log` and `.merge.log` with those strings emptied and their length kept
as `dropped_by_conductor` (`executor.compact_log`; a log under 1 MB is left alone).
The result line, what the executor said and every tool call stay, so the app's *Log*
view and `read_result` are unchanged; 18 MB typically becomes under 2 MB. The full
session, images included, stays in Claude Code's own store (`conductor open`). At
daemon start the same pass runs over every finished item's logs, which also compacts
logs from before this existed. A failure is a `warning`; the log is left as it was.

**Report header.** The report starts with:

```
status: done | partial | red
blocks: <what this blocks, or none>
carry: <what state.md should change, or none>
pick: <manifest path relative to the repo root>     # optional: only after writing a picker manifest
```

(Bare lines or inside a `---` block; `**status**:` is tolerated.) Only the first four
non-blank lines are read: `status`, `blocks` and `carry` count only in the first three
(the first of each), and `pick:` only as the fourth, after all three, with a one-word
value; so a body that opens with "Status: …" or "Pick: went with …" stays body. Backticks
around its value are dropped, `\` becomes `/`, and `pick: none` is no pick. The `finish`
event carries it as `pick: <repo>/<path>`, the ref the `pick:` signal takes.

**Outcome.** The item's final status is the report's status unless something makes it
red: no report or a bad header, no `result` line in the log, `is_error` in the result,
killed at `max_hours`, interrupted (restart rule), control-folder tamper, a readonly
item that changed its repo, a launch failure (worktree or setup included), a
`paths` violation, uncommitted changes left in the worktree, or a rebase conflict
(including a prose conflict its merge executor failed to resolve).
The reasons are listed in the `finish` and `red` events.

## Audits and templates

Code-quality audits are conductor-owned texts, not prompts the planner retypes. They
live in `src/conductor/templates/` (`audit.md`, `cleanup.md`, `general_audit.md`,
`test_perf.md`, `disk_audit.md`, `claude_md_cleanup.md`) and are versioned with this repo: the `start` event of a template item
carries `template` and `template_version` (the text's sha256, first 12 hex digits), so
`git log` on the file finds the exact text sent. Executors never see the control
folder, so whatever a template needs is inlined into the prompt (kept, as always, in
`.prompt.md`). The texts use `{{name}}` placeholders.

- **`audit: true`** (any item; explicit, never automatic). The preamble gains an
  *Audit notes* paragraph (`audit.md`) naming an absolute path,
  `reports/<repo>__NNN_name.audit.md`, as the second file the executor may write
  outside its working directory. It keeps a running notes file there while it works,
  covering every task, under four headings: what was poorly structured, what took
  longer than expected and why, what should be refactored or cleaned up, and what
  CLAUDE.md or README should say. Nobody reviews notes per item. The item's `finish`
  event carries `audit: true` and lists the notes file among its paths.
- **`template: cleanup`**. The prompt after the preamble is `cleanup.md` with every
  *unused* note of the repo inlined, oldest first, each headed `### Notes from <item>,
  finished <status> <time>`; then, after `---` and "The planner adds, for this item:",
  the item's own body, if any. The executor carries out what is clear and well
  defined and defers every design-level call (its report lists them under
  **Deferred**, with options and a recommendation). A cleanup never stages.
- **`template: general_audit`**. The same, with no notes to go on: it behaves as
  `audit: true` (it writes its own notes file), audits the whole repo, then carries out
  the clear items and defers the design calls.
- **`template: test_perf`**. Set up or tighten a fast/slow lane split and speed tests
  up generally; a small loss of thoroughness or moving a slow test to the slow lane is
  allowed; the report gives before/after timings per lane (the one report where test
  detail belongs). It uses no notes.
- **`template: disk_audit`**. Find why the project takes so much disk and free what
  can safely go, under the project's storage policy (new-project doc, *Storage*):
  delete what is cheaply rebuilt or clearly obsolete, move what is expensive or
  irreplaceable but not needed day to day into `archive\` under the project's Drive
  home, never delete anything hand-made; history rewrites and other destructive calls
  go under **Deferred**. The text overrides the preamble's main-checkout rule only for
  deleting untracked, cheaply rebuilt files there, and requires links (Drive junctions,
  `shared` links) to be removed as links before any recursive delete. At launch,
  conductor retries the repo's leftover worktree removals, then inlines what only it
  sees (the `{{usage}}` placeholder): the main checkout and its `shared` folders; the
  control folder's sizes by location (run logs, with the repo's share and the five
  largest, the rest of `reports/`, `events.db`, `.git/`, `.conductor/`, `done/`,
  everything else; `audits.control_usage`, a walk that never follows links); and each
  folder under the repo's worktree root, marked live (never touch), a leftover removal
  conductor retries, a red item's kept worktree, or stray. The executor never deletes a
  worktree folder itself. The report gives a before/after size table by location. A
  disk audit never stages.
- **`template: claude_md_cleanup`**. A quick pass through the repo's `CLAUDE.md`:
  cut what is extraneous or duplicated (a README fact gets a pointer, not a copy), fix
  what is wrong against the code, tighten the wording, keep the standing contract the
  new-project doc requires. Changes to how executors work go under **Deferred**. The
  report is short: **Cut**, **Fixed**, **Deferred**.

**Which notes are unused.** A note is a finished item whose `finish` event has
`audit: true` and whose `.audit.md` exists and is not blank, in any status (a red
item's notes are still notes). It is *used* once an `audit_used` event names it, and
*claimed* while a running cleanup's `.run.json` lists it under `notes`. A cleanup takes
every note neither used nor claimed at the moment it launches (they are in its `start`
event as `notes`). When it concludes, unless it ended red, conductor logs
`audit_used` (`notes`, and their files as `paths`) before its `finish`, so the next
cleanup sees only newer notes. A red cleanup uses none: they stay for the next one. A
general audit that does not end red uses its own notes the same way. A cleanup that
could run but has no unused notes holds (`hold`: *cleanup: no unused audit notes*)
until an audit item finishes or it is cancelled. While something else still blocks
it, it only waits, since the item it waits on may add notes.

`conductor audits [<control>]` lists each repo's unused notes (item, status, finish
time, file, line count); `--all` adds the used ones with the cleanup that used them.
Whenever a repo has notes no cleanup has used or claimed, it shows an **audit pending**
marker, so Matt can see a cleanup is due whenever it next makes sense. The inbox's *Now*
line for the repo and `conductor status` (under the repo) say
`audit pending: N audit notes (NNN_x, …)`. The app's Repos tab ends the repo's header
with `· audit pending: N audit notes`, drawn amber. The marker clears as soon as a
cleanup claims the notes (from when it launches) or uses them. All three share
`audits.pending`.

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

## Progress and messages

**Progress.** The executor's second tool, `progress` (`mcp__conductor__progress`):

```
progress(note, eta?, done?, remaining?)     # eta: free text (5:40pm, 25m); done/remaining: numbers
```

It returns at once (`Logged; carry on.`). The ask server appends a line (`seq`, `at`,
`note` — whitespace collapsed, 500 chars — and the rest) to
`.conductor/progress/<repo>__<NNN_name>.jsonl`; the daemon logs one `progress` event
per new line (`seq` dedupes across restarts). The latest line of a running item shows
in the inbox's *Now*, `conductor status`, the app's Repos tab and the Activity view;
`progress` events are left out of the inbox's *Other* section.

**Messages.** `tell: <item> <message>` is a signal verb (the app's **Tell…**, `conductor
tell` — which writes `signals/cli-….signal` with `by: cli` — and the planner all use
it). The message is the rest of the line, ` # ` included (it is not a comment there);
the app joins a multi-line message with ` / `. It applies only to an executing item
(`running/`, phase `exec`, process not exited): a queued or staged item is refused as
not started, a merging or finished one as exited, an unknown one as no running item.
The daemon logs `tell` (text, by) and writes `.conductor/mail/<repo>__<NNN_name>/<tell
id>.json`.

Delivery is a Claude Code **PostToolUse hook**. The executor's `--settings` file
(`reports/<repo>__NNN_name.settings.json`) names one hook on every tool (`matcher: ""`):
`<python> -m conductor.talk hook --control … --item …` (the `command` + `args` form, so
no shell quoting; 15s timeout). After each tool call it claims every waiting message
by renaming `<id>.json` → `<id>.delivered.json` and prints
`{"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": …}}`:
`MESSAGE #<id> from Matt via <by> …`, the text, and that it is an instruction from
Matt that wins over the prompt and is answered with `progress`. With no mail it prints
nothing. A `progress` call also hands over any message still waiting (the rename makes
either path deliver it once). The daemon logs `delivered` (tell, via `hook` or
`progress`, text, waited_seconds). An item that exits with messages still waiting has
them renamed `.undelivered.json`, and a `warning` names each.

Proven with Claude Code 2.1.301 headless (`-p`, stream-json): a message sent while a
real executor worked through a run of `sleep 25` Bash calls reached it after the call
in progress, and it changed course (see `reports/07_heads_report.md`). The injected
context is not written to the stream-json log; the `delivered` event is the record.
The user's own `~/.claude/settings.json` hooks still run beside it (`--settings` adds
to them).

**Short calls.** Since a message only gets in between tool calls, conductor keeps
every executor tool call to about a minute, the headless stand-in for pressing Ctrl+B
in an interactive session. Word Warriors 034 shows why: a tell waited 461 s because
the executor, having rightly backgrounded its test run, waited on it with one
foreground `timeout 590 bash -c 'until grep -q EXIT …; do sleep 10; done'` call; that
run made 20 such calls of 1–10 minutes, about 113 minutes in all. Two parts:

- The preamble's *Short calls* paragraph: run anything that may take over
  `foreground_seconds` (default 60) with `run_in_background`, wait on it in polls of at
  most that long, and don't end the turn to wait (an idle session takes no message).
- A **PreToolUse hook** on `Bash|PowerShell` in the same `--settings` file:
  `<python> -m conductor.talk pre --cap <foreground_seconds> --background=<glob> …`.
  A call already `run_in_background` is left alone. A command matching one of the
  repo's `background` globs (`fnmatch` on the whole command with runs of whitespace
  collapsed, case-sensitive, `*` crossing spaces: `npm test*`, `*playwright test*`)
  gets `run_in_background: true` and loses its `timeout`. Any other call whose
  `timeout` is missing (Claude Code's default is 2 minutes) or longer than the cap gets
  `timeout` = the cap. It prints `{"hookSpecificOutput": {"hookEventName":
  "PreToolUse", "permissionDecision": "allow", "updatedInput": …}}`, or nothing.
  `foreground_seconds = 0` with no globs drops the hook and the paragraph.

What makes the cap safe (Claude Code 2.1.304, `-p`): a foreground Bash call that
reaches its `timeout` is not killed but **moved to the background** ("Command did not
complete within its 60s timeout and was moved to the background (ID: …)"), the tool
call ends there, and the session is notified when the command finishes, as for one
started in the background; `-p` keeps the session alive until its background commands
end. A command moved this way is killed 10 minutes after the move (Claude Code says
so in the result; measured: moved at 15 s, killed at 10m15s); one started with
`run_in_background` has no such limit, which is what the `background` globs are for: a
suite that can run longer.

Proven in the sandbox with real executors (2.1.304) and a 4-minute stand-in suite, a
tell sent mid-run each time: told only by the preamble, the executor backgrounded the
suite and polled in 55 s calls, and the tell landed in 21 s (sandbox 047); told by its
prompt to run the suite as one foreground call with `timeout` 600000, the hook cut it
to 60 s, the call came back after ~73 s as moved, the tell landed in 52 s, and the
executor polled the moved command's output to the end (048). Both reported the
suite's summary and failing test correctly and acted on the tell. The 60 s is a
`timeout`, so a call can run a little past it (spawn and hook time under load).

**Amendments.** An amendment adds to an item's instructions without rewriting its file,
in any state but finished. The planner writes `queue/<repo>/NNN_name.amend.md` beside
the item (the item may be queued, staged or already in `running/`; the amend file
always goes in `queue/`), and more as `NNN_name.amend.2.md`, `.amend.3.md`, …, taken in
that order. Optional front-matter carries `by:` (`by: planner`; none is `unknown`); the
body is the amendment, any length. The app's **Amend…** and `conductor amend <item>
<text…|file>` (one argument naming a file sends its contents; `by: cli`) write the next
free name, as `.tmp` then renamed. An amend file is never an item: no `enqueue`, no
`edited`, and a stage is kept. Each tick, after enqueues and stage checks, the daemon
takes every amend file at least `SIGNAL_SETTLE_SECONDS` old (an item whose amend file is
younger waits a tick before it launches or resumes), logs `amended` (text, by, state,
delivery; its id is the amendment's number) and moves it into the item's mailbox
(`.conductor/mail/…/<id>.json`, `kind: amend`). Delivery by state:

- **queued** (not staged): appended to the prompt at launch, each under `## Amendment #n
  (5:25pm, via planner): it amends the prompt above and wins where they conflict`.
- **staging / staged**: the stage is kept; at resume the amendments follow the resume
  message, which tells the executor each amends the prompt it read and overrides the
  plan it made where they conflict.
- **running**: through the message hook, whole (multi-line), as `AMENDMENT #n from
  Matt via <by>, an addition to the prompt you are running …` with a tell's closing
  wording.
- **merging / finished**: refused (a correction to finished work is a new item):
  `amend_refused` (red: inbox, toast) with the reason, the file left where it is and
  refused once per content (a changed file is judged again). An amend file naming no
  item yet logs one `warning` and waits; it is taken once the item appears, so an amend
  file written with its item still lands at launch.

`delivered` follows each (amend, via `prompt`, `resume`, `hook` or `progress`), and
every amendment is appended to `reports/<repo>__NNN_name.amend.md` as delivered: number,
when amended, by, state, when delivered and via, and the text, so a postmortem shows
what the executor was told and when. One the executor exited before taking is marked
there as never delivered, with a `warning`. A reorder carries an item's waiting
amendments to its new name.

**Limits:** a message still arrives only after a tool call ends: not while a blocking
`ask` waits, not while the executor sits idle at the end of a turn waiting for a
background command's notification (the preamble asks it not to), and never once it
has made its last tool call (then it is undelivered). A non-shell tool that runs long
(an Agent / Task subagent, a Workflow) is not capped.

**Open a finished session.** `conductor open <item>` (and **Open in terminal** in the
app) opens a new console window running `<executor command> --resume <session id>`
(the session id from the item's `finish` event) in the item's worktree while it exists,
else the repo's main checkout: `--resume` finds a session from any folder. Refused
while the item is running or staged (resuming a live session forks it). Windows only;
elsewhere the error prints the command to run.

## Run targets

A project's long-running thing (a dev server, a game) is a **run target** in
`conductor.toml`:

```
[run.game]
repo = "game"                  # run in this repo's main checkout; or cwd = "<folder>" (relative to the control folder)
command = "npm run dev"        # a string runs through the shell; or a list: ["python", "-m", "http.server", "8765"]
url = "http://localhost:5173/" # opened in the default browser
ready = "http://localhost:5173/health"   # optional: what must answer first (default: url)
ready_seconds = 60             # optional: how long to wait for it
```

The signal `run: <name>` (planner, app) or `conductor run-target <name>` (CLI):
- **Already up** (the ready URL answers HTTP, any status): the URL opens at once,
  `run_open`.
- **Down:** the command starts detached (as the app starts the daemon: no window, its
  own process group, out of the caller's job; a daemon stop leaves it running), output
  appended to `.conductor/run-<name>.log`, pid and creation time in
  `.conductor/run-<name>.json`; `run_start`. When the ready URL answers, `run_ready`
  (waited_seconds) and the URL opens, `run_open`.
- **Starting already:** its recorded process is alive but not answering yet, so nothing
  new starts (`run_start` with `reused`). One process per target: a dead recorded
  process is simply started again.
- **Never comes up:** the process exits first, or `ready_seconds` pass (the process is
  then left running): `run_failed`, a red kind, with `error` and the log's last 20 lines
  (`log_tail`). It leads the inbox's Red section and toasts, but holds no queue.

`run: <name> stop` (`conductor run-target <name> --stop`, the app's **Stop <name>**)
kills the recorded process tree; `run_stop`. The daemon never blocks on a start: it checks pending targets every
tick. A daemon restart forgets a start it was waiting on (run it again: it opens at
once if up). The CLI waits in the foreground and prints the events. On Windows a probe
of a closed local port can take up to its timeout (1s; the app's header probe uses
0.3s per target per refresh). The planner adds or changes targets with a
`conductor.toml` request.

A repo's main checkout has no installed dependencies: executors install them in their
worktrees, which are removed after the merge. So a target whose server needs them
installs them first, e.g. `command = "npm install && npm run dev -- --port 5173"`.

## Picks

When an executor produces candidates (art variants now, anything visual later), Matt
picks a favourite per row in his browser, adds a note where none is right, and saves;
the planner reads his choices to write the next prompt. One generic page serves every
project (`src/conductor/web/picker.html`); it knows nothing about what it shows. Code:
`picks.py` (manifest, choices, what is pending; no HTTP) and `picker.py` (the server).

**Manifest.** A JSON file the executor writes in its code repo; the preamble's
*Pickers* paragraph gives it the schema, since executors never see this README:

```json
{"name": "art_alts_009",
 "title": "Hero art alternatives",
 "intro": "optional text above the rows",
 "rows": [{"id": "hero", "label": "Hero, idle pose", "note": "optional",
           "options": [{"key": "current", "image": "hero/current.png"},
                       {"key": "1", "image": "hero/alt1.png", "label": "warmer"}]}]}
```

It is named by its ref, `<repo>/<path relative to the repo root>` (normalised: `\`
becomes `/`, `.` and doubled separators go), and read from the repo's **main
checkout**, never a worktree. Validation, on every `pick:`; the error names the place
(`rows[2].options[1].image: missing file art/alt3.png`):

- the path is relative, has no `..`, and stays inside the repo; the file exists, is at
  most 1 MB, and is UTF-8 JSON whose top level is an object. "Inside" is judged on the
  path as written, links not followed, so a Drive-homed `shared` folder (a junction out
  of the main checkout) counts as the repo's;
- `name`: a slug, `[a-z0-9][a-z0-9_-]{0,63}` (it becomes a URL part and a file name),
  unique per project: a `pick:` is refused while the daemon's server serves that name
  for another manifest, or while `picks/<name>.choices.json` holds another manifest's
  choices; `title`: a non-empty string; `intro`: optional string;
- `rows`: a non-empty list; each row's `id` a non-empty string unique in the file,
  `label` a non-empty string, `note` an optional string, `options` a non-empty list;
- each option's `key` a non-empty string without whitespace, unique in its row (it is
  what the next prompt uses: `"current"`, `"1"`, …); `label` an optional string;
  `image` a path relative to the manifest (`..` allowed while it stays in the repo; no
  absolute path or drive), an existing file ending `.png .jpg .jpeg .gif .webp .avif .svg`;
- unknown keys are ignored, so the format can grow.

By convention the first option of a row is the current one, and it is the default; a
row whose choice differs from it is *changed*.

**Surviving the merge.** The manifest and its images must be on `main` by the time the
`finish` event is logged, which is what the `pick:` line points at: commit them, or put
them in a `shared` folder (linked into every worktree, so present in the main checkout
already). An item with `paths:` must cover where it writes them. A `readonly` item is
never merged (its worktree is discarded), so it must use a `shared` folder.

**Signal and server.** `pick: <repo>/<manifest path>` (planner, app, `conductor pick`)
validates the manifest, starts the picker server if it is not up, and opens the
picker's page in the default browser (`pick_open`; `pick_failed`, red, if opening the
browser raised). A bad manifest, or a server that cannot start, rejects the signal file
(`invalid`, `.bad`, with the reason). The server:

- listens on `127.0.0.1` only, on a free port, started by the first `pick:`: **one per
  daemon**, serving any number of pickers, keyed by `name`. It stops when the daemon does;
- URLs are `http://127.0.0.1:<port>/<token>/<name>/`, the token random per daemon start,
  so other pages in the browser cannot find it;
- routes: `GET …/` (the page), `GET …/manifest.json` (the manifest as the page reads it,
  with the saved choices), `GET …/img/<row>/<option>` (by index into the validated
  manifest; no part of a URL is ever joined to a file path), `POST …/save`;
- **everything else is a 404**: another token, name or index, an extra path part, a
  query on an image, any other method (HEAD, PUT, DELETE, OPTIONS, …), a `Host` that is
  not `127.0.0.1:<port>` or `localhost:<port>` (DNS rebinding);
- a save must be `application/json` (a cross-site form cannot send that without a
  preflight the server never answers), at most 1 MB; a bad body is a 400 with the
  reason, a choices file that cannot be written a 500. Saves are one at a time (two
  tabs on one picker cannot interleave their writes). SVGs carry
  `Content-Security-Policy: sandbox`, every response `X-Content-Type-Options: nosniff`.

The manifest is a snapshot taken at `pick:` time; re-send `pick:` to serve an updated
one (same name: replaced). Images are read from disk on each request; one deleted since
is a 404. Handler threads touch only files and a queue: the daemon's loop appends their
events (`pick_saved`) each tick. **A daemon restart loses the server** (port, token and
what it serves): an open tab's Save then fails, and the page says so and keeps its
choices. Re-send `pick:` and save from the new tab.

**The page.** One row per `id`: label and note, then the options as tiles captioned
`key · label`, about 256px tall (a size slider, 128–640px, remembered by the browser).
Click a tile to select it; click the selected tile, or its magnifier corner, for a
full-size overlay where ←/→ step through the row and Enter picks the one shown. **The
page never alters how an image looks**: no opacity, filter, tint or overlay on any
image, selected or not, in the tiles or the overlay; nothing is drawn over one but the
magnifier button. The selection shows only outside the image: a strong accent outline
around the tile and a caption in the accent colour with a check and bold key. Each
row starts on its saved choice, else its first option; a changed row is badged. A note
field per row. A sticky bar: "N of M changed · K notes", *show changed only*, and
**Save** (Ctrl+S), which shows "Saved 4:52pm". Leaving with unsaved changes warns.
Light or dark by system setting.

**Choices.** Save writes `picks/<name>.choices.json` in the control folder (git-ignored
runtime, like `questions/`), overwriting any earlier save:

```json
{"name": "art_alts_009", "title": "Hero art alternatives", "manifest": "code/picks/art_alts_009.json",
 "saved_at": "2026-10-18T16:52:03-07:00", "changed": 6, "notes": 2,
 "rows": [{"id": "hero", "key": "2", "changed": true, "note": "hair still too orange"}]}
```

Every manifest row appears once, in manifest order, with a key from its own options;
`changed` is worked out by the server, never taken from the page; notes are stripped,
at most 4000 characters, possibly empty. Reopening loads the save (the page shows "last
saved 4:52pm"): rows match by `id`, and a key no longer offered falls back to the first
option with its note kept. A choices file whose `saved_at` is not a stamp with a zone is
ignored, as if unreadable.

**Pending.** A finished item whose `finish` carries `pick` is *saved* when a choices file
for that ref was saved after the finish, else *opened* when a `pick_open` for it came
after the finish, else *waiting*. So a later item that reuses a manifest path starts
again at waiting. Every such item in the current view is pending; so is one settled by a
checkpoint while its picker is unsaved and no later finish names the same manifest, so
a picker never drops out unpicked (saving it, even unchanged, lets it settle). The
inbox's *Picks* and the app's **Pick…** buttons both read it; the app keeps an unsaved
one among the current finished rows, however old.

**CLI.** `conductor pick <repo>/<path>` validates first (`conductor: <reason>`, exit 1,
and a red `pick_failed` event with the reason, as the signal's `invalid` would be).
With the daemon alive it writes `signals/cli-….signal` (`by: cli`), and the daemon opens
the picker. With no daemon it serves the picker itself in the foreground, printing the
URL and each event as it is logged, until Ctrl+C. Those events go to `events.db` as the
daemon's would.

## Toasts

The daemon raises a Windows toast (library: `windows-toasts`, WinRT in-process) for
`ask`, `red`, `tamper`, `hold`, `run_failed`, `pick_failed` and `queue_empty` events. Each title names the project and
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

The front-matter is YAML: a `message:` containing a colon must be quoted
(`message: "state: v0.2"`). Unquoted, the YAML fails and the request lands `.failed`.

Conductor waits until nothing in the folder has changed for 2 seconds, checks the
guard, copies the files in (UTF-8 text is written with LF endings; anything else byte
for byte), stages and commits exactly the targets, logs `request` and `commit`, removes
the request folder and re-snapshots. Every file in the folder must be a target and
every target must be present; targets must be relative, inside the folder and outside
the runtime paths. A bad request is logged `request_failed` (red) and renamed
`requests/<stamp>.failed/`. A request that changes nothing is logged with
`no_change` and removed.

## Signals: read, clear, approve, cancel, reorder, reviewed, tell, checkpoint, run, pick, stop

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
tell: sandbox/005 use sqlite # a message to running sandbox/005 (see Progress and messages)
checkpoint: session start    # a session boundary (see Checkpoints)
run: game                    # start run target `game` if needed, open its URL (`run: game stop`)
pick: code/picks/art_alts_009.json   # serve that picker manifest and open it in the browser
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
- **tell** — `tell: <item> <message>`: see *Progress and messages*.
- **checkpoint** — `checkpoint: <label>` (any text, a ` # ` included, or none): see
  *Checkpoints*.
- **run** — `run: <target>` or `run: <target> stop`: see *Run targets*. An unknown
  target is refused.
- **pick** — `pick: <repo>/<manifest path>`: see *Picks*. The value is one word (a
  manifest path with a space, or ` #`, cannot be named) and its repo must be configured,
  or the file is rejected at parse time; a manifest that fails validation rejects it
  when applied, with the reason.
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

## Checkpoints

A checkpoint is a session boundary, so each planner session starts with a clean view.
`checkpoint: <label>` (planner, usually `checkpoint: session start` with `by: planner`;
the app's **New checkpoint…**; `conductor checkpoint [label]`, `by: cli`) logs a
`checkpoint` event (label, by). **Its number is its event id**: nobody keeps a counter.
Only the latest checkpoint matters to the views.

- **Hidden:** a finished item whose `finish` event and first `reviewed` event both come
  before the latest checkpoint, unless its red still holds its queue (`clear:` not yet
  sent). Such an item is *settled*.
- **Carried forward, whatever their age:** unreviewed reports, an item reviewed only
  after the checkpoint (until the next one), red holds, open questions, and anything
  queued, staged, running or proposed. None of these is a finished-and-reviewed item,
  so none is ever hidden.
- **Warning:** at the checkpoint, every report still unreviewed gets a `warning` naming
  it (item, `checkpoint`, `finish`, `note`), so the new session raises it first.

Nothing is deleted or moved: reports, `done/` and `events.db` stay as they are, and
`conductor events`, a postmortem, the app's trail and `conductor open` reach any item.
What changes is only the default views:
- `inbox.md` names the current checkpoint (number, label, time, by) under its header
  and how many settled items it hides; an unreviewed report older than the checkpoint
  is tagged *carried from before checkpoint #N*.
- The app's Repos tab lists only current finished items, with the settled ones in a
  collapsed *earlier checkpoints* group per repo. The Events tab starts at the latest
  checkpoint (*since checkpoint #N*, on by default; off shows the whole log).
- `conductor events --since checkpoint` (or `checkpoint N`) starts at a checkpoint;
  `conductor status` names the latest one.

## Inbox

`inbox.md` is regenerated whenever the log or the read mark changes (and every 30s
for the *Now* section). Its header names the current checkpoint (see *Checkpoints*).
First **Questions**: every open ask, regardless of the read
mark. Then **Unreviewed reports**: every finished item without a `reviewed` event,
oldest first, with its status, finish time, finish event id, report path and `carry:`
line (and ` · pick: <ref>` when its report named a picker); it shows on every inbox,
read mark or not, until a `reviewed:` signal names the item. Then **Picks**, also
standing (see *Picks*, *Pending*): each pending picker, then any picker saved since the
checkpoint (any time without one) that no pending item names, as the saved line
without the item:

```
- art_alts_009 saved 4:52pm: 6 changed, 2 notes; `picks/art_alts_009.choices.json` (code/012_art_alts)
- code/013_more opened 4:40pm, not saved — `code/picks/more.json`
- code/014_faces waiting for Matt — signal `pick: code/picks/faces.json`
```

Then every event after the read mark, grouped **Red** (tamper first,
then `red`, `conflict`, `paths_violation`, `kill`, `request_failed`, `invalid`, `run_failed`,
`pick_failed`),
**Holds**, **Finished** (status, blocks, carry, pick, report path), **Questions resolved**
(answers, defaults taken, late answers, closes), **Requests and commits** (merges
included), **Other**; then **Now**: per repo, what is running (or merging), staged
(`staging`/`staged`), queued, waiting or held, and *audit pending* if notes are unused,
plus proposed items. A hold stays in
the Holds section until the item starts, merges, finishes or is cancelled, or a
reorder renames it (its old id is gone; the renamed item gets its own hold). `enqueue`, `start`,
`stage` and `worktree` events are left out of the inbox (they are in
`conductor events`). `conductor status` lists the same per repo: running (pid, branch,
kill time), merging (with the reason it waits), staging/staged, queued, and the
*audit pending* line when the repo has unused audit notes.
`conductor inbox` prints the same thing without moving the mark.

## The app

`uv run conductor app [<control folder> …]` (or the `conductor-app` gui script, no
console) opens a PySide6 window with one tab per control folder. Folders named on the
command line are added; the list is remembered across launches (QSettings, org
`conductor`, app `conductor-app`, key `controls`) and edited with *Add project…* /
*Remove project*. A remembered folder that fails to load (drive not mounted,
`conductor.toml` broken) is reported in the status bar and stays remembered. Qt is
imported only by this command; the daemon and CLI never need it.
**One app per user.** The app listens on a per-user local server (`QLocalServer`
`conductor-app-<user>`: a named pipe on Windows, a socket file elsewhere). A later
`conductor app [<folder> …]` connects, sends `{"folders": [...]}` as one JSON line and
exits 0; the running app adds those folders (remembered), selects the last one named
and comes forward. So repeating a launch never adds a second tray icon, and no second
app rewrites the remembered list from its own stale copy. If the name can't be claimed
the app runs alone, as before. Code: `app/single.py`.
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
files, its own settings, starting the daemon, and opening a console on a finished session.

**Header.** The project's state, whether its daemon is alive (`.conductor/daemon.json`,
pid + creation time), and a count of the app's signals still waiting in `signals/` (or
rejected as `.bad`). *Start daemon* spawns `<python> -m conductor run <folder>` (the
console `python.exe` even when the app runs under `pythonw.exe`; git calls pass
`CREATE_NO_WINDOW` too, so nothing flashes a console window)
detached (no window, its own process group, out of the caller's job object when
allowed), output appended unbuffered (`python -u`) to `.conductor/daemon.out.log`; *Stop daemon…* asks first,
then writes a `stop: daemon` signal. Executors and stages keep running either way.
Each run target gets a **Run <name>** button beside them, reading `● up`, `● starting`
or `● down` (green, amber, grey; probed each refresh); a click writes `run: <name>`,
which opens the URL (starting the target first when it is down). While a target is
up or starting, a **Stop <name>** button beside its Run button writes `run: <name>
stop` (no confirmation: a click is cheap to undo with Run).
**New checkpoint…** asks for a label (default `session start`) and writes
`checkpoint: <label>`; its tooltip names the current checkpoint.

**Panels.**

- *Repos* — a tree: each repo row, then up to three group rows, each item under its
  group. The repo row's note is a summary in the same words as the items' State
  cells, each item counted once (`1 running · 1 staged · 2 queued · 1 unreviewed`,
  then `N to pick` and the *audit pending* marker; `held` / `held after red` first
  while held; `idle` when there is nothing). Group rows are muted bold labels with
  their count (`In flight (3)`, `Finished since checkpoint (1 unreviewed)` — plain
  `Finished` before any checkpoint — and `Earlier checkpoints (29)`), and nothing in
  State, Note or the button column; an empty group is not shown. Every repo row and
  group can be collapsed, and each keeps the state it was left in across refreshes
  (*In flight* and *Finished* start open, *Earlier checkpoints* closed). A hold row
  sits directly under the repo, before the groups (the next item then reads *blocked
  by hold*); a red hold has a **Clear hold** button (`clear: <repo>`).
  *In flight*: the running item with its phase (`running`, `merging`, `concluding`)
  and elapsed time, then the queue in order, the next one marked `· next` with its
  decision (`wait`, `stage`, …) in the note, and any invalid item. A staged item is
  still queued, so it is one row whose state carries both: `staging` (with elapsed) or
  `staged` (note: plan ready), then `· next` or `· blocked by hold`; other queued items
  read `queued`. Each queued item has **Cancel** (asks first;
  `cancel: <repo>/<NNN_name>`).
  *Finished* holds the repo's current finished items (those not settled by a
  checkpoint), newest first: the
  10 most recent plus every unreviewed one however old, each with its status, finish
  time, carry and a *reviewed* / *unreviewed* mark (unreviewed bold, coloured by
  status; reviewed muted). Double-click opens the trail, as for live items. Each
  unreviewed row has **Mark reviewed** (`reviewed: <repo>/<NNN_name>`, `by: app`; the
  button disables once clicked and the row turns *reviewed* when the daemon logs it).
  The tab label reads *Repos (N unreviewed)*, in amber, while any report is unreviewed.
  A running item's note shows its latest progress line (note, ETA, done / to go, and
  when it was sent); while it executes it has **Tell…**, a text box whose message goes
  out as a `tell:` signal (`by: app`). A queued row (beside **Cancel**) and an executing
  one (beside **Tell…**) have **Amend…**, a multi-line box written as an amend file
  (`by: app`; see *Amendments*). Every finished row has **Open in terminal**
  (`claude --resume`, see *Progress and messages*), beside **Mark reviewed** while
  unreviewed. A finished row whose report named a picker has **Pick…** (`pick: <ref>`,
  `by: app`; amber until saved) and its note adds ` · pick: saved 4:52pm (6 changed, 2
  notes)`, ` · pick: opened, not saved` or ` · pick: waiting for Matt` (see *Picks*,
  *Pending*); the tab label adds `N to pick` while any is unsaved. Items settled by the
  latest checkpoint sit in *Earlier checkpoints* (the 10 newest; the count is all of
  them).
- *Questions* — open asks, blocking ones first, then by soonest default; each shows
  *BLOCKING* or the default and a live countdown to it, its options as buttons that
  fill the answer box (the default's marked *(default)*), and **Answer**, which writes
  `questions/<id>.answer.md` through the same function as `conductor answer` (never
  overwriting one), with `by: app` front-matter so the `answer` event names the app. An answer to a question that resolved meanwhile (its default taken)
  is reported as late, as the CLI does. Resolved questions are listed beneath, newest
  first. Question files are re-parsed only when they or their result change.
- *Events* — by default the **Activity** view: workflow-level lines only, worded
  plainly — `prompt X started`, `report X written — done/partial/red (carry: …)`,
  `report X reviewed`, `X used N audit note(s)`, `X: <progress line>`, `message #n to X: …` and `message #n
  reached X`, `amendment #n to X, while <state> (…)` and `amendment #n reached X`, run targets starting / up / opened / stopped, pickers opened / saved,
  questions asked / answered / default taken (and late answers and
  closes), approved, cancelled, reordered, cleared, a queued item rewritten (`edited`), `doc update committed: <message>`
  (a `request`) or `request failed`, daemon started / stopped, and every red, `warning`
  or `invalid` event whatever its kind. A `finish` is the *report written* line, not a
  separate entry. Everything else (`worktree`, `stage`, `staged`, `merge`, `commit`,
  `hold`, `enqueue`, `queue_empty`, `read`, …) appears only with **Advanced** checked
  (off by default), which also shows each event in the log's own wording. The *Kind*
  list offers only the kinds the current mode shows. `new checkpoint #N "label"` is an
  Activity line. **since checkpoint #N** (on by default) starts the list at the latest
  checkpoint's own event; off, it is the whole log. In both modes: filterable by repo, item (`001`, `001_x`, `repo/001_x`, or
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
  Double-clicking an item in any other panel opens its trail here. The trail's
  **Open in terminal** is enabled for an item that has finished (and is not queued or
  running again).
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
| `edited` | a queued item's file changed before it launched (it runs the new text) | staged (its stage is discarded) |
| `invalid` | bad item, signal, stray queue folder, bad config | error |
| `hold` | a queue head cannot run, or a merge waits (reason changed) | reason; merge |
| `start` | executor launched | session_id, pid, deadline, worktree, branch; resumed, landed (a staged item); template, template_version, notes (a template item; a cleanup's notes); error (launch failed) |
| `stage` | a blocked queue head launched read-only | session_id, pid, worktree, deadline |
| `staged` | the stage finished cleanly | session_id, plan (first 500 chars), cost_usd |
| `worktree` | created, moved (a staged worktree to current main), removed (merged), kept (red), discarded; a leftover removed on retry | action, branch, base, note (a shared folder created; `leftover removed on retry`) |
| `merge` | an item's branch landed on main | branch, sha (main after), commits; resolved (prose files a merge executor resolved) |
| `resolve` | the rebase stopped on prose files only; a merge executor was launched to resolve them | files, step, commit (the one being replayed), session_id, pid, deadline |
| `conflict` | the rebase onto main conflicted, or a merge executor failed (red) | branch, files, error; resolver (the merge executor's log) |
| `paths_violation` | the branch changed files outside `paths` (red) | files, paths_declared |
| `adopt` | restart found the executor (or stage: `stage: true`) alive | pid, session_id |
| `recover` | restart found it finished while the daemon was down, or a merge to resume | note |
| `requeue` | restart found it never launched | reason |
| `kill` | max_hours reached | pid, reason |
| `finish` | item done (after its merge, when it had one) | status, report_status, reasons, blocks, carry, pick (`<repo>/<path>`, or null), session_id, exit_code, cost_usd, turns, branch, merged; audit (it kept notes), template |
| `audit_used` | a cleanup (or general audit) concluded, not red: the audit notes it acted on | notes (item ids); paths (their files) |
| `red` | the item's status is red | reasons, after_red |
| `tamper` | control folder changed during an executor's window | changes, concurrent |
| `drift` | control folder changed with no executor running | changes |
| `request` / `request_failed` | a request applied or rejected | request, message, targets / error |
| `commit` | conductor committed the control folder, or an item committed in its repo | scope, sha, subject |
| `read` / `clear` | signals | through / repo |
| `reviewed` | a `reviewed:` signal marked a finished item's report as acted on | finish (its event id), status, by |
| `checkpoint` | a `checkpoint:` signal started a new session view (its id is its number) | label, by |
| `progress` | the executor called `progress` | seq, at, note, eta, done, remaining |
| `tell` | a `tell:` signal queued a message for a running executor (the event id is the message id) | text, by |
| `delivered` | the message or amendment reached the executor | tell or amend, via (`hook`/`progress`; an amendment also `prompt`/`resume`), text, waited_seconds; kept (an amendment's `.amend.md`) |
| `amended` | an amend file was taken (the event id is the amendment's number) | text, by, state (`queued`/`staging`/`staged`/`running`), delivery |
| `amend_refused` | an amend file for a merging or finished item, or empty or unreadable (red; the file stays) | state, reason, path, hash, by |
| `run_start` / `run_ready` / `run_open` / `run_stop` | a run target started (or was already starting: `reused`), answered, had its URL opened, was stopped | target; pid, command, cwd / waited_seconds / url / note |
| `run_failed` | a run target never came up, or its URL could not be opened (red; holds no queue) | target, error, log_tail |
| `pick_open` | a `pick:` signal (or `conductor pick`) served a manifest and opened its page; item: the latest finish that named it, if any | name, manifest, url, opened (false: the browser reported it did not open); by |
| `pick_saved` | Matt saved a picker; paths: its choices file; no `by` (the page has no identity) | name, manifest, changed, notes, rows |
| `pick_failed` | opening the browser raised, `conductor pick` refused the manifest, or its own server could not start (red; holds no queue) | manifest, name, url, error; by |
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
| `config_reload`, `warning` | config applied; runtime path not ignored; a toast failed; a stage discarded; a shared path that is a file, or not ignored; a worktree not removed (recorded for retry) or still not removed on a retry; a report still unreviewed at a checkpoint | note; checkpoint, finish (the unreviewed-at-checkpoint warning) |

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

The suite runs against a fake executor (`tests/fake_claude.py`, which obeys `FAKE …`
lines in the prompt), so the scheduling, guard, request, restart, question, worktree,
merge and staging paths are covered without spending Claude runs. It runs in two lanes,
in parallel (`pytest-xdist`, up to 8 workers):

```
uv run pytest            # the fast lane, after every change (~25s, 136 tests)
uv run pytest -m slow    # the slow lane alone (~50s)
uv run pytest -m ""      # everything (~70s, 186 tests); run this before committing
uv run pytest -n0 tests/test_x.py::test_y    # one test, no workers (startup ~3s otherwise)
```

`@pytest.mark.slow` marks a test that took 2.5s or more serially: several executors, a
merge or prose conflict, a stage, a restart, or a real wait on a clock. The time is
real git and process spawns (~30ms each on Windows), not sleeps, so parallelism is the
lever. Keep new tests deterministic under load: hold an executor open with `env.hold()`
(a `FAKE wait-for` line) and let it go with `env.release()` rather than a fixed `FAKE
sleep`, which races a loaded machine. Every `Env` copies a code repo and control folder
built once per worker. The fake runs the real message hook only when a message is
waiting.
A staging prompt makes the fake obey only `FAKE stage-…` lines and remember the prompt
under its session id; `--resume` recalls it, so a test can see that a resumed session
kept what it read while staging. `FAKE pick <path>` adds `pick: <path>` to the report
header after `carry:` (a test writes the manifest and images with `FAKE commit` first);
picker tests stub the browser and bind port 0. `FAKE ask {json}` calls the
real ask server through the item's `--mcp-config` using `tests/mcp_client.py`, a
scripted MCP client that also runs standalone:
`python tests/mcp_client.py <mcp.json> '{"question": "…", "default": "…"}'`. The sandbox
(`C:\Code\conductor-sandbox`, `C:\Code\conductor-sandbox-control`) is for end-to-end
runs with real executors.
