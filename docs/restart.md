# Restarts and processes

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

**Mid-setup** (run record `phase: setup`, or a stage record's `setup.state: running`;
`executors.md`, *Setup*): never adopted. The setup runner's tree is killed (by pid and
creation time, or found by its spec path on a command line). A run's worktree is
discarded (note `daemon stopped during setup; requeued`), the item goes back to
`queue/` and `requeue` is logged (reason `daemon stopped during setup`). A stage is
discarded (`the daemon stopped during the stage's setup`). No executor ran, so the rule
holds.

## Self-restart

A daemon restarts itself when conductor's code changes. The code version is `git -C
<conductor checkout> rev-parse HEAD:./src HEAD:./pyproject.toml`, so only a commit that
changes `src/` or `pyproject.toml` counts: uncommitted edits and commits touching only
docs, prompts or tools never do. Not a git repo, or git failing: the feature is off.
The daemon reads the version at start and again every 10s (`CODE_CHECK_SECONDS`),
between ticks, after `stop: daemon` is handled.

On a change it first checks the new code imports (`<console python> -c "import
conductor.cli, conductor.daemon"`). It restarts only at a safe point. Blockers: the
checkout itself not settled (uncommitted or untracked changes under `src/` or in
`pyproject.toml`, or a rebase, merge or cherry-pick in progress: the new daemon runs the
files on disk), a merge executor resolving (`resolve.state: running`), a setup running
(above), an unfinished reorder journal, a run target still starting, or a picker opened
in the last hour (`pick_open` with no later `pick_saved`) that it still serves (a
restart loses the picker's port and token). Blocked past 5 minutes
(`RESTART_PATIENCE_SECONDS`): one `warning` naming the blockers; it keeps waiting and
never forces it.

At the safe point it first applies pending signals: a `stop: daemon` among them wins (a
normal stop, no restart). Then blockers and imports are checked once more, and the new
daemon is spawned detached (`<console python> -u -m conductor run <control> --after <old
pid>`, output appended to `.conductor/daemon.out.log`). The new process writes
`.conductor/restart.ready` once its config loads and the daemon imports, then waits up
to 60s for the old pid to exit. The old daemon waits up to 20s for that file
(`RESTART_READY_SECONDS`); only then does it log `daemon_restart` and exit cleanly
(picker closed, inbox written, lock released, no `daemon_stop`), and the new one adopts
executors and keeps stages as for any restart. A version that fails (no import, no
spawn, no ready file; a hung new process is killed) gets one `warning` naming
`daemon.out.log`; the old daemon keeps running and tries that version again after 5
minutes (`RESTART_RETRY_SECONDS`). A daemon started in a terminal hands that terminal
back when it restarts itself. The app's Start/Stop and "restart all" are unchanged.

## Platform notes

Windows-first, cross-platform by construction. Process handling specific to Windows
is in `src/conductor/procs.py`: executors start with `CREATE_NEW_PROCESS_GROUP |
CREATE_NO_WINDOW` on Windows (`start_new_session` on POSIX) so Ctrl+C or closing the
daemon's console doesn't reach them; processes are identified by pid **and** creation
time (pids are reused); kills take the whole tree (psutil). All files conductor writes
are UTF-8 with LF endings.

Windows still records the daemon as the executors' parent, so `taskkill /T` on the
daemon kills them too. To stop only the daemon use Ctrl+C or `taskkill /F /PID <pid>`
without `/T`. So never start the daemon as a Claude Code background command: when that
session ends, or reaps its background commands under memory pressure, the daemon and
every executor die with it (2026-10-20, `memory.md`). A daemon that finds a Claude Code
session among its parents logs a startup `warning`. Start it from the app, a plain
terminal, or `conductor run --detach <control>` (the app's spawn: it waits up to 60s for
`daemon.json`, prints the pid, and leaves a live daemon alone).

A long-lived process another one starts (the daemon from the app's **Start daemon**, a
run target from the daemon) goes through `procs.spawn_detached`: a short-lived Python
launcher starts it (own process group, no window, out of the caller's job when the job
allows breakaway) and exits, so the process has no live parent and no tree kill of the
caller reaches it. What it returns is a `Detached` (pid, creation time), not a
`Popen`: the exit code of a process that isn't your child is never known. Executors,
stages, merge executors and setup runners stay the daemon's own children, so their
exit codes are kept.
