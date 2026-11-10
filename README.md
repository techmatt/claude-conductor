# claude-conductor

Conductor is a daemon that runs queued Claude Code prompts for one project. It is
pointed at the project's **control folder**, runs approved prompts from per-repo
queues as headless `claude -p` sessions (each in its own git worktree, merged into
`main` when it finishes; one at a time per repo unless an item is readonly or
`alongside`), and records everything in an append-only event log from which the inbox and
every other view is derived.

`planner.md` is how a planner session uses it (rare ops in `planner-vocab.md`);
`claude-ai-new-project.md` is how a project is started, parked and resumed. This file and
`docs/` are how conductor works: the canon for commands, file formats, fields and event
kinds, a topic per file. `CLEANUP.md` is the maintenance pass, with each file's size budget.

## Install and run

```
uv sync
uv run conductor run    <control folder>   # the daemon, foreground; Ctrl+C stops it
uv run conductor run --detach <control folder>   # detached, as the app's Start daemon; safe from Claude Code
uv run conductor status [<control folder>]
uv run conductor inbox  [<control folder>]
uv run conductor events [<control folder>] [--since X] [--item Y] [--repo R] [--kind K] [--paths]
uv run conductor questions [<control folder>] [--all]      # open asks (--all: resolved too)
uv run conductor audits [<control folder>] [--all]         # unused audit notes per repo (--all: used and claimed too)
uv run conductor answer <id> <text…> [--control <folder>]  # writes the answer file
uv run conductor tell <item> <text…> [--control <folder>]  # a message to a running executor
uv run conductor outline <item> [--control <folder>]       # ask a running executor for an updated outline
uv run conductor amend <item> <text…|file> [--control <folder>]  # add to an item's instructions (docs/progress.md)
uv run conductor checkpoint [<label…>] [--control <folder>]   # a new checkpoint (docs/inbox.md)
uv run conductor session [<label…>] [--control <folder>]      # a checkpoint plus the session.md digest (docs/inbox.md)
uv run conductor open <item> [--control <folder>]          # a finished item's session, in a new console
uv run conductor run-target <name> [--stop] [--control <folder>]   # start a [run.<name>] target, open its URL
uv run conductor pick <repo>/<manifest path> [--control <folder>]  # open a picker in the browser (docs/picks.md)
uv run conductor open-path <folder|reveal|file|text|url> <target…> [--control <folder>]  # the open: verbs (docs/open.md)
uv run conductor init <control folder> --project NAME --repo NAME=PATH [--repo …]
uv run conductor rename-repo <old> <new> [--path P] [--control <folder>]   # daemon stopped (docs/rename-repo.md)
uv run conductor app [<control folder> …]                  # the desktop app (docs/app.md)
```

The control folder argument defaults to `$CONDUCTOR_CONTROL`, then the current folder.
`conductor app` with no folder adds `$CONDUCTOR_CONTROL` if set, else the current folder
only when it holds a `conductor.toml`; it always opens its remembered projects too.
One daemon per control folder: a second `run` refuses while the first is alive.

`--since` takes an event id (`#12` or `12`, exclusive), a relative age (`30m`, `2h`,
`1d`), a clock time today (`5:25pm`), an ISO time, `checkpoint` (from the latest
checkpoint's own event on) or `checkpoint N` (from checkpoint #N on). `--item` takes `sandbox/001_x`,
`001_x` or `001`.

Stopping the daemon does **not** stop executors (the next start adopts them); a **tree**
kill does, so start it from the app or a terminal, never as a Claude Code background
command (`docs/restart.md`).

## Topics

| File | What it covers |
|---|---|
| `docs/control-folder.md` | Layout, `.gitignore`, `conductor.toml`, `conductor init`, the tamper guard, requests |
| `docs/rename-repo.md` | `conductor rename-repo` |
| `docs/runtime.md` | The `.conductor/` folder: lock, heartbeat, journals, scratch |
| `docs/items.md` | Queue items: front-matter, rewriting, `depends_on`, max_hours; legacy `paths:` concurrency and prose files |
| `docs/scheduling.md` | When an item starts: NNN order, holds, readonly and `alongside` items, exclusive templates |
| `docs/worktrees.md` | Per-item worktrees, merges, prose conflicts and the merge executor, leftover worktrees; staging |
| `docs/push.md` | Pushing main and the control folder, `push:`, closeout; failures |
| `docs/executors.md` | Launching an executor: MCP and settings files, `wait_for`, delivery, long calls, setup |
| `docs/preambles.md` | What it is told: the full, light or watch preamble, its port, scope, scratch and load |
| `docs/reports.md` | The report's header and an item's outcome |
| `docs/logs.md` | Run logs: compaction and gzip |
| `docs/ultracode.md` | `ultracode: true`: how it turns on |
| `docs/templates.md` | Audit notes and the conductor-owned templates (the cleanups, bug hunt, self audit, watch) |
| `docs/night-shift.md` | Stall check, watch item, autopilot, the backlog and its draws, nightly budget |
| `docs/resume.md` | Resume items: asking a finished item's own session (forked); self-audit |
| `docs/questions.md` | The `ask` tool, answers, defaults, blocking questions |
| `docs/progress.md` | `progress`, outlines, messages (`tell:`), short calls, amendments, opening a finished session |
| `docs/signals.md` | Every signal verb; how a signal file applies |
| `docs/open.md` | Desktop: `open:` (paths, URLs), `notify:` toasts |
| `docs/receipts.md` | Each planner write's receipt: where, what it says, how long it lives |
| `docs/inbox.md` | Checkpoints, the `session.md` and `review.md` digests, `inbox.md`, toasts |
| `docs/run-targets.md` | `[run.<name>]` targets: start, open, stop |
| `docs/picks.md` | Picker manifests, picker server and page, saved choices |
| `docs/remote-workers.md` | Remote workers: `[remote]`, `farm-seeds`, local-remote worker, removal |
| `docs/remote-status.md` | The worker page; the app's chip, Workers tab |
| `docs/remote-url.md` | The one worker URL: PC name, pinned token, handover |
| `docs/remote-diag.md` | `remote-diag.md`, steps, page logs |
| `docs/remote-placement.md` | Who runs a shard; `main_slots`; leases |
| `docs/remote-cores.md` | A browser's core count |
| `docs/parked.md` | Parked edits: `parked/<repo>.md`, which item carries them, what goes back |
| `docs/memory.md` | Memory and load sampling, pressure levels, launch guard, leftover reaper, a daemon's death |
| `docs/app.md` | The desktop app: one per user, what it reads and writes, header, actions, project tools, tray |
| `docs/app-panels.md` | The app's tabs: Repos (with the Conversation view), Questions, Events, Proposed, Workers |
| `docs/events.md` | `events.db` and every event kind with its detail |
| `docs/restart.md` | The restart rule, self-restart on a code change; process groups, detached launches |
| `docs/development.md` | The test suite, its lanes and the fake executor |
| `docs/known-limits.md` | Accepted gaps, by area |
