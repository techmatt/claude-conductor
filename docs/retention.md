# Disk retention

Conductor keeps its own disk use bounded without being asked (147: aicomplete's control
folder held 35.9 GB and a kept worktree a 2.7 GB `target/`). `disk.py` and
`remote/retention.py`.

## When it runs

- The daemon sweeps hourly, the first sweep 10 minutes after it starts
  (`daemon_reap.keep_disk`), in a thread with its own event-log connection so a walk over
  gigabytes never holds the loop; a sweep still running is never doubled; one that fails logs a
  `warning` and is tried the next hour.
- `conductor prune [<control>] [--dry-run]` sweeps at once and prints a line per rule:
  what it removed (or would) and what it kept, with sizes.

Every removal is a `pruned` event with its size (`events.md`).

## Kept worktrees: rebuildable output

A red item's worktree is kept for inspection (`worktrees.md`). Once nobody needs to look, its
rebuildable output goes; the sources, `out/` and the branch stay, so `land:` and `retry:`
still work (a retry rebuilds). Due when:

- its last `finish` has a `reviewed` event (the planner reviewed that finish), or its repo
  has a `clear` after it;
- it isn't running again (no `start` since that finish) and wasn't swept since (a `pruned`
  is logged once even when nothing was there, so the worktree isn't walked again).

What goes: every folder named in the repo's `rebuildable` (`[repos.<name>]`, default
`["target", "node_modules"]`, `[]` turns it off; folder names, not paths) at any depth, but
only one git ignores (`git check-ignore`), never under the worktree's top `out/`, never through
a shared link or other junction, and only the outermost match. `pruned` detail: `what: build
output`, `path`, `dirs` (relative path → bytes), `bytes`.

## `.conductor/remote/`

In a project with `[remote]`, newest first:

| What | Pruned | Bounds (`[remote]`) |
|---|---|---|
| `jobs/<id>/` | past its age, or the oldest past the size cap in all (the newest within its age always stays) | `keep_jobs_days` (2), `keep_jobs_gb` (5) |
| `bundles/<key>` with its `.build.log`, `.spawn.log`, `.failed` | past the count or its age | `keep_bundles` (20), `keep_bundles_days` (3) |
| `trees/<n>/` and `<n>.json` | while more than the bound exist, the highest-numbered one no live process holds (a free tree is reused lowest first) | `keep_trees` (3) |
| `rev-*/` | a `--at` extraction a killed run left, after a day | none |

Never pruned: a job the broker still has open (`board.json`), one whose `farm-seeds` is alive
(`owner.json`), or one a queued or running item's file names (`j<digits>-<n>`); a bundle such a
job, a held farm tree or an item's file (a 12-hex key) uses, one being built (a `.tmp` or a
lock under 45 minutes old), or the worker test's. A job's age is its newest of the folder,
`shards/`, `job.json` and `main.log`. `pruned` detail: `what` (`farm jobs`, `farm bundles`,
`farm trees`), `removed`, `bytes`, `names` (the first 20), `kept`, `kept_bytes`. A pruned
bundle whose key is farmed again is built again: a new job resets the board's `ready` for a key
whose bundle folder is gone. The broker's own seven-day sweep of `jobs/` at its start is gone.

The keys are `[remote]`'s but not the broker's: changing one never restarts it.

## Measured (147)

aicomplete before: `.conductor/` 35.9 GB: `remote/jobs` 30.95 GB (367 jobs: shard outputs
28.8 GB, `main/` leftovers 2.2 GB), `remote/trees` 2.71 GB (6 trees), `remote/bundles` 1.19
GB (305 keys), `kept/` 1.04 GB (3 items). `kept/` was already bounded (`worktrees.md`, *Kept
reads*).

After the first `conductor prune` (Nov 22): 7.48 GB: jobs 5.00 GB (142 kept, 225 removed,
25.96 GB), bundles 0.08 GB (20 kept, 285 removed), trees 1.35 GB (3 kept, 3 removed), `kept/`
1.04 GB; 112_price_correction's `target/` (2.68 GB) removed from its worktree. The sandbox
lost 1 job and 11 bundles; the other projects had nothing to prune.
