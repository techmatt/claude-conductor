# farm-seeds: its source and stopping it

What `farm-seeds` says about the code it runs, and how a run is stopped without leaving
work behind (114; `src/conductor/remote/client.py`). The command itself, its shims and
the item's prompt paragraph: `remote-workers.md`, *The executor interface*.

## Its output file

`<out file>`'s folder is made first if missing (118: aicomplete 238 farmed 1,152 games,
then crashed at its merge because `out/keep/` didn't exist).

## The farm tree it runs from (130)

With `[remote] pin` (default `true`), every step of a run happens in a **farm tree**, a copy
of the repo's files (tracked, plus untracked and unignored: the set the job key hashes) made
at its start: `native_build`, the fingerprint, the main box's native shards, a local-remote
worker's (the job's `cwd`) and the wasm build (which holds the tree while it runs, even past
the job). Its second stderr line says `farm-seeds: runs from farm tree <path> (the code as it
is now; N file(s) copied in)`. `src/conductor/remote/tree.py`; tests `test_remote_tree.py`.

- **Why.** Each step used to read the item's working tree while the executor edited it:
  aicomplete's bundle `32fccf1` (263) compiled configs that had `city_cut_at` with code that
  didn't (the commit had neither), so Node and the browser both failed it; a parameter added
  mid-farm failed a whole farm's native shards (263); and a build beside a farmed read failed
  with "Access is denied" on the `target/release` exe its shards held (167, 260).
- **Where.** `.conductor/remote/trees/<n>/`, with `<n>.json` (its key, the files it copied, its
  holders). Runs of one key share a tree; a tree nobody holds is synced to the next key
  (changed files only, with a fresh mtime, so builds stay incremental: `target/` stays in the
  tree); none free, a new one. Deleting `trees/` while no farm runs only costs a full build.
- An argument naming a file the tree lacks (a spec in a git-ignored folder) is copied in at
  the same place; a file outside the repo is read where it is.
- The item's own `target/` is never built or locked by a farm: an item that runs the native exe
  itself builds it itself. `pin = false` runs in the working tree as before 130.

## A named commit: `--at` (133)

`farm-seeds --at <commit> <first seed> <count> <out file> [args…]` (or `--at=<commit>`) builds
and farms that commit instead of the working tree, so both sides of a paired read farm
(aicomplete 270 ran its old-revision side natively and was flagged for it). The commit's files
come from `git archive` into a farm tree (`tree.take(..., rev=)`), always, `pin` or not; its
key is the commit's first 12 hex digits, as a clean tree there would have, so the bundle
cache is shared. An argument naming a file the commit lacks is copied in from the working
tree; one it has keeps the commit's version. Its first line reads `farm-seeds: native from
commit <sha> (--at <rev>; the working tree is not used)`; a rev that isn't a commit exits 2.
Synced from a commit, a tree compares files by hash, never size and mtime: `git archive`
stamps every file with its commit time, so two commits in the same second looked unchanged.

## The commit it runs from

Its first stderr line names the commit the native command runs from:
`farm-seeds: native from commit <sha>` (12 characters). On a dirty tree (a tracked change
or an untracked, unignored file: the same test as the job key, `repo_key`) that line adds
"plus uncommitted changes", and a second follows:

    farm-seeds: warning: uncommitted changes (<up to 5 paths> and N more): the farm builds
    from them, but no commit holds the code this read ran, and a bundle build that takes
    only committed code would run different code. Commit before you farm

Mixed sources used to pass silently (aicomplete 213); since 130 the farm tree holds the
uncommitted changes for both builds, but a read is still only reproducible from a commit.
It warns and carries on; it never refuses.

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
