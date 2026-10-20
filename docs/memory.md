# Memory

Code: `memory.py`, with the Windows parts (`GetPerformanceInfo`, the launcher) in
`procs.py`. Found live, 2026-10-20 (`reports/20_memory_pressure_report.md`): at 2:16am
a Claude Code session judged the box low on memory and reaped its background commands.
One of them was the app; the app had started the daemon, and the daemon the executor,
so the tree kill took all three and nothing was logged until 7:01am. Windows itself
never logged a low-memory event.

**Samples.** Every `memory_sample_seconds` the daemon reads the box's commit charge
against its limit (RAM plus page file: what runs out on Windows) and free physical
memory, and walks each live item's process tree (the executor, a stage, a merge
executor, and everything under them) by kind: `claude` (the executor with its
in-process subagents and workflows), `chromium`, `vite`, `playwright`, `node`,
`python`, `shell`, `other`, with count and private bytes (RSS off Windows). One
compact JSON line per sample goes to `.conductor/memory.jsonl` (GiB, kinds in MiB,
items biggest first). Past 4 MB the file keeps only the last `memory_keep_hours`.
Each sample also rewrites the heartbeat (`beat` in `daemon.json`). No event is logged
per sample.

**Levels.** `ok`, `pressure`, `critical`: commit at or over `memory_*_percent` of its
limit, or free RAM at or under `memory_*_free_gb` (0 turns a threshold off). A level is
left only when it is clear by 3 points or 0.5 GiB, and has stayed clear for
`memory_calm_seconds` (300); a rise counts at once. So a box hovering at a threshold
doesn't flap: found live, Alienware's `OCControl.Service` on this box grew its private
memory by ~3 GiB a minute and was recycled near 9–10 GiB, swinging free RAM by gigabytes
every few minutes. Free RAM is the main signal: with an auto-managed page file Windows grows
the commit limit on demand, so commit sat at 87% through an ordinary test run on this
box (limit 36.6 GiB idle, 40.6 under load), which is why the commit defaults are high. On each change the daemon logs `memory` (into or out of pressure) or
`memory_critical` (red, toasted). Each names the commit, limit, percent and free GiB,
the five biggest item × kind rows, the three biggest process names outside conductor's
items (a full scan, so only on a change), and what conductor does now.

**Guard.** While the level is not `ok`, nothing new starts: no launch, resume or
stage (the queue keeps its order, and the inbox's *Now* line and `conductor status`
say *launches and stages paused*). Each running item is sent a `tell` (`by:
conductor`, `MEMORY_TELLS` in `daemon.py`) when the level changes: at pressure, cut
parallelism (one browser and one test worker, close idle servers, no new parallel
subagents or workflows); at critical, one process at a time and commit work so far;
back at ok, it may return to normal. A running executor is **never** killed for
memory. A critical level also says, in its event and toast, to close what Matt can.

**Leftovers.** When an item's executor exits (any outcome), and when a stage is
discarded, conductor kills what it left running in its worktree (`reaped`, with each
process and the MiB freed). A leftover is an **orphan** (its parent is gone: it
outlived the executor or the shell that started it) started while the item ran (5s
before its start, up to now) whose working folder is the worktree or under it, or a
tool (`chromium`, `vite`, `playwright`, `node`, `python`, `shell`) whose command line
names the worktree. It is killed with everything under it. A terminal or editor Matt
opened on the worktree has a live parent, so it is never matched. Never reaped: a run
target's process tree, another item's or a stage's, or the daemon and its parents. The
scan reads creation times and psutil's parent map first (a working folder costs ~10ms
per process), about 0.15s on a box of 400 processes. Items run in the main checkout (`worktrees = false`)
are never reaped. A leftover worktree's removal retry (`leftovers.json`) reaps first,
with the window from the item's `start` event to its `finish`: found live, 030's Vite
preview and twenty waiter loops (`tail`, `grep`, `bash`) held its worktree for a day.

**Death made visible.** A clean stop removes `daemon.json`, so one left by a process
that is gone means the daemon died. The next start logs `daemon_died` (red, toasted)
with the dead pid, its last heartbeat, how long nothing ran, the last memory sample,
and what was in `running/`. The app's header and `conductor status` say **died** (with
the last heartbeat) instead of *not running*, and **not answering** for a live daemon
whose heartbeat is more than three samples (at least 90s) old.

**Staging is cheap already.** A staged item holds no process while it waits for its
lock: the stage session ends with its plan, and the resume is a new
`claude --resume`.
