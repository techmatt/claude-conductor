# Remote workers: placement

Which machine runs a farm job's shards, and how many the main box runs at once. The
deal is `src/conductor/remote/placement.py` (pure); `Board.poll`, `Board.claim` and
`Board._share` feed it. Leases, drops and the broker going away are here too.

## The job

- No broker (or one not answering within half a second): the native command runs at once
  over the whole range. A lease is never waited out.
- A job estimated under `local_below_seconds` stays local.
- Otherwise the range is split into shards of `shard_seeds`, **whether or not a worker is
  connected**. A worker that joins mid-job takes shards at once (89: 600 seeds begun with
  none, a local-remote worker joining 8 s in took 15 of 24; 15.1 s against 33.2 s local).
- Sharding costs the main box little (89, sandbox, no worker): 28.5 s against 27.3 s bare,
  ~0.5 s fixed plus ~30 ms a shard, 17 ms of it a hidden console (`CREATE_NO_WINDOW`,
  not `DETACHED_PROCESS`: its children could open windows).

## Who runs a free shard: expected finish (100)

A free shard goes to the worker expected to finish it soonest. Each time a worker asks
for work (a browser core's poll, a local-remote worker's, a main-box slot's claim), the
job's free shards are dealt one at a time, each to the slot that would finish it first:
when that slot is free (now, or when its shard is expected done) plus the worker's time
for a shard. The asker gets a shard only if the deal gives one to a slot of its that is
free now; otherwise it waits (a main-box slot polls again in 0.2 s, a browser core in
0.5 s), and the shard stays for the faster worker.

- **A worker's time for a shard**: its ms a seed a core measured in this job. Before that,
  the main box's rate here times the ratio last measured between the two (`Board.ratios`,
  kept in `board.json`; jobs differ in cost, so only the ratio carries over). A worker
  never measured beside the main box is taken at the main box's rate. Before the main box
  has a rate here, it is derived from a measured worker and its ratio, else the mean of
  what is measured, else `seed_seconds`.
- **Slots**: the main box's `main_slots`; a remote worker's cores. A busy slot is free at
  its lease plus its expected time; an overdue one is taken as needing a whole shard more.
  A remote worker's free cores count only while it polls (within 5 s): a tab that stops
  asking isn't waited for. Only workers that can take the job count: heard from, gated for
  its commit, with cores, not draining, paused or in error.
- **A tie** goes to a remote worker over the main box, and to the one asking over other
  remote workers. So an unmeasured tab, timed at the main box's rate, gets work and a rate.
- **A worker that fails a shard** gets none of that job's other shards, and the deal no
  longer counts it (167: a laptop failed every shard of a spec its bundle lacked).

Aicomplete 168 (Nov 10): placement was first-come. The main box's 6 slots took a 4-shard
job whole while the laptop, faster a core, sat gated with 2 cores; a job of `main_slots`
shards or fewer never reached a worker. Sandbox walk (100): the main box slowed to ~7.2 s
a 25-seed shard, a headless Chrome tab ~4.0 s; a 4-shard job went to the tab entirely
(5.6 s, against 7.2 s on the main box alone).

## `main_slots` is over every job (100)

The main box runs at most `main_slots` shards at once over **all** open jobs: a claim
waits while that many are out on it, in this job or another. `farm-seeds` still starts
`main_slots` claiming threads per job; the broker holds the count. 168 ran two jobs at
once with 6 slots each: 12 `aic-sim` processes on the main box at 100% CPU, 160 s a shard
there against the laptop's 85 s. Sandbox walk: two 16-shard jobs at once with
`main_slots = 4`, never more than 4 native processes between them.

## The tail

Once none is left unleased, the main box re-runs the oldest shard still out on a worker
(within its `main_slots`). Before that, too (103): a main slot free while the deal keeps the
free shards for a remote worker copies any shard out only on remote workers past 1.5× its
worker's expected time (`board.OVERDUE`, `Board._overdue`), the most overdue first. A read
waits for its slowest shard: aicomplete's laptop ran one job's 2-seed shards 7 to 11 min
(j1794328936-30) while free shards still went its way. The first copy back wins, and a
shard the main box is running is killed when a worker's copy lands (the worker's is dropped
when the main box's lands: "another copy is in"). A copy the main box lands while a browser
holds the shard is a loss for that browser's core count (`remote-cores.md`).

A failed shard is free at once (its lease goes with the result), and the worker that failed
it gets none of that job's others, so the main box's free slots take them straight away
(100). A spec newer than a worker's bundle is not detected before the first failure: open.

## Leases (96)

A worker no longer heard from (`fresh_seconds`) loses its shards at once. One still heard
from keeps a shard while its check-ins name it (the page's hello and the local-remote
worker's beat list their cores' `[job, shard]` pairs: `Board.renew`), so a slow live
worker never loses one. One that never names it (an old page) loses it after
`lease_seconds` when set, else 4× the shard at the slowest ms a seed measured in the job
(at `seed_seconds` before any), never under 60 s (`Board.lease_for`). The main box's
leases hold while its job is open. 161: a flat 60 s against 2-6 min shards re-ran 33 of 48.

## Dropped shards and a broker that goes away

- **A shard nobody needs is dropped** (96): the page's hello reply names (`drop`) any shard
  its cores run whose job closed or whose other copy is in; the page replaces that core's
  Web Worker (a wasm run can't be interrupted) and the core goes idle. An idle core's poll
  also prompts a hello, so this holds in a hidden tab (its timers are throttled).
- A broker that stops answering mid-job is waited for 45 s (`client.PATIENCE`: a restart
  takes a few); one still gone then fails the command at once (111): `farm-seeds: error: the
  broker went away mid-job (N of M shards in, kept in <job>/shards)`, exit 3, nothing written
  to the out file (aicomplete 209-211 each lost 6-12 min to the old silent fall back to one
  shard at a time; the line ends a `wait_for` on its `fail` pattern). A main-box slot that
  fails: the job is closed and the shards not yet in run locally.
- `farm-seeds` calls the broker every 2 s while its job runs; a job not heard from in
  `fresh_seconds` is no longer served (a killed item's job never starves the next) and is
  dropped after `lease_seconds` (60 s when unset).
