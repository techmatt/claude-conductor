# farm-seeds: its source and stopping it

What `farm-seeds` says about the code it runs, and how a run is stopped without leaving
work behind (114; `src/conductor/remote/client.py`). The command itself, its shims and
the item's prompt paragraph: `remote-workers.md`, *The executor interface*.

## The commit it runs from

Its first stderr line names the commit the native command runs from:
`farm-seeds: native from commit <sha>` (12 characters). On a dirty tree (a tracked change
or an untracked, unignored file: the same test as the job key, `repo_key`) that line adds
"plus uncommitted changes", and a second follows:

    farm-seeds: warning: uncommitted changes (<up to 5 paths> and N more): the native exe is
    built from them, a wasm bundle may take only committed code, so shards can run
    different code. Commit before you farm

`native_build` runs in the working tree, while a project's wasm build may take only
committed code (aicomplete's `scripts/farm/build.mjs`), so mixed sources used to pass
silently (aicomplete 213). It warns and carries on; it never refuses.

## Stopping a run

- **`farm-seeds --cancel`** stops this item's runs (`CONDUCTOR_REMOTE_ITEM`); `--all`, every
  item's. A run keeps `owner.json` (`{pid, item}`) in its job folder while it lives; cancel
  kills that process tree (only if the pid is still a farm-seeds, so a reused pid is safe)
  and closes the job on the broker, so workers drop its shards at once (`Board._unwanted`).
  It says `farm-seeds: cancelled N run(s) of item <id>` and exits 0.
- **Ctrl-C, Ctrl-Break or SIGTERM** set its cancel: the main-box slots stop, their native
  runs are killed, the job is closed and it exits 130 (`CANCELLED_EXIT`), writing nothing
  to `<out file>`. A run that stayed local stops the same way, though its native command
  may have begun writing `<out file>`.
- **A hard kill** (TerminateProcess, as stopping its shell may do) runs no handler. Every
  native shard it starts sits in a kill-on-close job object (`procs.die_with_me`, Windows,
  nested in the executor's own), so the shards die with it and nothing is left locking
  `target/release/aic-sim.exe` (aicomplete 212, 214). Its job stays on the broker until
  the next `farm-seeds` (any item's) closes every job whose owner is gone, or the broker
  drops it once its heartbeat has been quiet `max(lease_seconds or its floor, 3 ×
  fresh_seconds)` (`Board._active`): 10 minutes at aicomplete's `lease_seconds = 600`, which
  is why a stopped run's job kept workers busy.

`native_build` (cargo) is not in that job: a build killed mid-way runs to its end.

Tests: `src/conductor/remote/tests/test_remote_cancel.py`.
