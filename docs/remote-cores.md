# Remote workers: the broker chooses a browser's core count

A browser worker runs on as many cores as actually help, chosen by the broker from what that
worker delivers (99). Nobody sets or remembers a slider. The policy is
`src/conductor/remote/cores.py`; `Board` feeds it and keeps the plans. The main box's
`main_slots` and the local-remote worker's `--cores` are unchanged.

Why: aicomplete 163 (Nov 9). The laptop tab on all 7 cores won 1 shard of 7, the main box
re-ran the other 6 at the tail, and the read ran slower than a local one. On 2 cores the same tab
ran 1.22 seeds a minute a core, against the main box's 1.12-1.17, and won its shards.

## The rule

Within one job, a count's per-core rate (ms a seed of each shard, run with that many cores
busy in the tab) is compared with the best rate at any lower count measured in the same job.
Not the count just below: the sandbox walk's first version crept from 2 to 9 cores on a tab
pinned to 4 CPUs, each step within 15% of the last. Another
job's arguments can cost more a seed, so jobs are never compared with each other, and a new
job starts its measurements over.

| | |
|---|---|
| start | a new name starts at **2** |
| samples | a count decides on its rate once **2-4** shards are in at it (its own count, at most 4) |
| raise | **+1**, when the rate held (median within **20%** of the best lower count's, or none measured yet in this job) and no shard was lost at it; never past the tab's cores or its cap |
| back off on rate | median more than **20%** slower than the best lower count's: back to the count below |
| back off on losses | **2** or more of the last **4** shards at this count lost to the main box (and at least half): back to the count below measured in this job, else half, rounded up (7 → 4) |
| ceiling | a count backed off from is not tried again for **an hour** |

20%, not less: a CPU's all-core clock is below its one-core clock, a fall that is no fault.

A loss is the main box's copy of a shard landing while the tab still held it (the tab's
copy is dropped, `remote-workers.md`, *Placement*), or the tab's copy arriving second. A
shard is counted at the cores it was handed out under (the tab's last hello said how many
were running), and only while that is the plan's count: a shard begun before a raise and
finishing after it ran beside the new cores, and would drag the old count's rate down.

## Under the worker's name

The plan (count, last change and its reason, the ceiling) is kept on the broker under the
worker's name (the page's Name box, else `Chrome on Win32` and the like), in `board.json`,
so it outlives a reload, a browser restart, a new tab and a new broker. A renamed worker starts
a new plan at 2. Two machines with the same automatic name share a plan: name each in its
Name box. The newest 100 names are kept.

## The page

- The page sends its hardware cores (`hw`) and its cap with each hello; the reply's `cores`
  is the broker's count and `why` its last change. The page starts no core before the first
  answer, then runs `min(count, cap)`.
- A raise starts cores at once. A cut drains the extra cores: each finishes the shard or
  gate it has (or its poll in flight), then stops. A running shard is never killed.
  **Pause** still stops every core at once.
- **The slider is a cap**, for when Matt wants the machine for something else. At its
  right end it is no cap (the default; an older page's remembered core count is not
  carried over as one). While a cap sits under the broker's count, the broker decides
  nothing for that worker: what it would measure is the cap's, not its own count's.
- Under the tiles: `4 cores chosen by the broker · no cap · 7 → 4: shards lost to the main
  box 3 of 4` (or `· cap 3: running 3`). Each change is in the event log, with the reason.

An older page (no `hw` in its hello) gets no plan and runs its slider as before.

## Where a change shows

- `remote-diag.md`, under each browser worker: `cores: 4 of 8 chosen by the broker; last
  change 10:52:31pm: 7 → 4: shards lost to the main box 3 of 4` (and `, capped at 3 on its
  page` while capped). The change is also a `core count` event among that worker's events (a
  `cores` note in `steps.log`).
- `status.json`'s worker rows carry `chosen`, `cap`, `hw`, `why` and `why_at`.

## Proof

Sandbox, 99: headless Chrome pinned to 4 logical CPUs (`start /affinity F`), so its rate
per core must fall past 4, and headless Edge unpinned, on the LAN URL; `sim --cost 600000`
(~190 ms a seed, 25-seed shards of ~5 s), 1,500-seed jobs back to back.

- Chrome: 2 → 3 → 4 → 5 (330, 363, 313 seeds/min a core), then `5 → 4: rate fell to 254
  seeds/min a core from 363 at 3`, 43 s after it joined. Restarted (a new tab id, the same
  name): `the broker runs 4 cores here`.
- Edge: 2 → 8 in 50 s, every step within 13% of its best (428 at 3; 372 at 7).

At ~0.6 ms seeds (16 ms shards) the rates are noise and the count swings: the rule is for
shards of seconds or more, which is what farming is for.
