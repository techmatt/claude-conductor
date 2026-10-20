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

A long-lived process another one starts (the daemon from the app's **Start daemon**, a
run target from the daemon) goes through `procs.spawn_detached`: a short-lived Python
launcher starts it (own process group, no window, out of the caller's job when the job
allows breakaway) and exits, so the process has no live parent and no tree kill of the
caller reaches it. What it returns is a `Detached` (pid, creation time), not a
`Popen`: the exit code of a process that isn't your child is never known. Executors,
stages and merge executors stay the daemon's own children, so their exit codes are kept.
