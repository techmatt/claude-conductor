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
uv run conductor init <control folder> --project NAME --repo NAME=PATH [--repo …]
uv run conductor run --detach <control folder>   # the daemon, detached (or the app's Start daemon)
uv run conductor app [<control folder> …]        # the desktop app (docs/app.md)
```

Every command and flag, `--since`'s forms and the control folder's default:
`docs/commands.md`. Stopping the daemon does **not** stop executors (the next start
adopts them); a **tree** kill does, so never start it as a Claude Code background
command (`docs/restart.md`).

## Topics

| File | What it covers |
|---|---|
| `docs/commands.md` | Every `conductor` command and flag; `--since`; the default control folder |
| `docs/control-folder.md` | Layout, `.gitignore`, `conductor.toml`, `conductor init`, the tamper guard |
| `docs/requests.md` | Planner requests: applying, waits, failures and order, edit blocks |
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
| `docs/retros.md` | Workflow retros: when one is due, conductor queuing it, the digest's line |
| `docs/warnings.md` | A report's `warnings:` line: a project's own checks, where warnings show |
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
