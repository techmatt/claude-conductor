# claude-conductor

Conductor is a daemon that runs queued Claude Code prompts for one project. It is
pointed at the project's **control folder**, runs approved prompts from per-repo
queues as headless `claude -p` sessions (each in its own git worktree, merged into
`main` when it finishes; one at a time per repo unless items declare disjoint
`paths`), and records everything in an append-only event log from which the inbox and
every other view is derived.

`planner.md` is how a planner session uses it, every session; `claude-ai-new-project.md`
is how a project is started, parked and resumed. This file and `docs/` are how conductor
works: the canon for commands, file formats, fields and event kinds. Each topic file is
small enough to read whole, and stays so: `CLEANUP.md` is the standing maintenance pass,
with every file's size budget (`uv run python tools/sizes.py`).

## Install and run

```
uv sync
uv run conductor run    <control folder>   # the daemon, foreground; Ctrl+C stops it
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
uv run conductor init <control folder> --project NAME --repo NAME=PATH [--repo …]
uv run conductor app [<control folder> …]                  # the desktop app (docs/app.md)
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

Stopping the daemon (Ctrl+C, or killing it) does **not** stop executors; the next start
adopts them. A **tree** kill does, so start the daemon from the app or a plain terminal,
never as a Claude Code background command (`docs/restart.md`).

## Topics

| File | What it covers |
|---|---|
| `docs/control-folder.md` | The control folder's layout and `.gitignore`, `conductor.toml`, `conductor init`, the tamper guard, requests |
| `docs/items.md` | Queue items: front-matter, rewriting, scheduling, holds, readonly items, max_hours; `paths:` concurrency and prose files |
| `docs/worktrees.md` | Per-item worktrees, merges, prose conflicts and the merge executor, leftover worktrees; staging |
| `docs/executors.md` | How an executor is launched: the preamble, its port, MCP and settings files, ultracode, long tool calls, setup, log compaction, the report header, the outcome |
| `docs/templates.md` | Audit notes and the conductor-owned templates (cleanup, general audit, test perf, disk audit, claude.md cleanup, bug hunt) |
| `docs/questions.md` | The `ask` tool, answers, defaults, blocking questions |
| `docs/progress.md` | `progress` and outlines, messages (`tell:`), short calls, amendments, opening a finished session |
| `docs/signals.md` | Every signal verb and how a signal file is applied |
| `docs/inbox.md` | Checkpoints, the `session:` start digest (`session.md`), the `review:` bundle (`review.md`), `inbox.md`, toasts |
| `docs/run-targets.md` | `[run.<name>]` targets: start, open, stop |
| `docs/picks.md` | Picker manifests, the picker server and page, saved choices |
| `docs/memory.md` | Memory sampling, pressure levels and the launch guard, the leftover reaper, a daemon's death made visible |
| `docs/app.md` | The desktop app: one app per user, what it reads and writes, the header, actions, tray |
| `docs/app-panels.md` | The app's tabs: Repos (with the Conversation view), Questions, Events, Proposed |
| `docs/events.md` | `events.db` and every event kind with its detail |
| `docs/restart.md` | The restart rule, the self-restart on a code change; platform notes (process groups, detached launches) |
| `docs/development.md` | The test suite, its lanes and the fake executor |
