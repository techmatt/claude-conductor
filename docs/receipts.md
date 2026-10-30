# Receipts

Every planner write gets a receipt: one small file saying what conductor did with it,
at a path the planner knows before it writes, so it stages that file instead of
re-reading `inbox.md` to see whether the write landed (`src/conductor/daemon_receipts.py`).

## Where

The write's path under `receipts/`, with `.signal` or `.md` changed to `.md`:

| write | receipt |
|---|---|
| `signals/planner-20261021-101500.signal` | `receipts/signals/planner-20261021-101500.md` |
| `requests/20261021-1015-state/` | `receipts/requests/20261021-1015-state.md` |
| `queue/<repo>/012_x.md` | `receipts/queue/<repo>/012_x.md` |
| `queue/<repo>/012_x.amend.<tag>.md` | `receipts/queue/<repo>/012_x.amend.<tag>.md` |
| `questions/<id>.answer.md` | `receipts/questions/<id>.answer.md` |
| `proposed/012_x.md` | `receipts/proposed/012_x.md` |
| `backlog/<repo>/bug_sweep.md` | `receipts/backlog/<repo>/bug_sweep.md` (`night-shift.md`; a draw rewrites it) |

Every signal file and request gets one, whoever wrote it (the app's too).

## What

```
---
write: "signals/planner-20261021-101500.signal"
outcome: "accepted"
result: "applied"
at: "10:15am"
written_ms: 1761059700123
events: [812, 813]
---
- #812 10:15am <the event, worded as in the inbox>
- #813 10:15am ...
```

- `outcome`: `accepted`, with `result` (what happened), or `refused`, with `reason`.
  A queue item's `accepted` receipt (enqueued or rewritten) may also carry `warning`: a
  `depends_on` that can never be met in order, so it will hold (`items.md`); fix it now.
  A signal refused part-way also has `applied`: the lines that applied before the one
  that failed (they stay applied). A request held by an earlier one on the same targets
  is `pending`, its `result` what it waits for, replaced by the final receipt; so is one
  still waiting on its own files 2s after `request.md` (`PENDING_AFTER_SECONDS`):
  `waiting for targets missing: state.md`, or `waiting for a changed copy: every target is
  identical to HEAD so far (…)` (`control-folder.md`, *Requests*). Younger, it has no
  receipt yet: missing means not handled yet.
- `events`: the ids of every event its handling logged, one line each below: a
  signal's, side effects included; a request's `request`, `commit` (and a `closeout:`
  request's `read`). Past eight (`LINES`; a `session:` warns once per unreviewed
  report) the body ends `… and N more`.
- `written_ms`: the write's mtime in ms when conductor took it (absent for a signal or
  request, gone by then). A rewritten queue or proposed item overwrites its receipt:
  one whose `written_ms` is older than the rewrite is the previous version's.

| write | written when | result | refused when |
|---|---|---|---|
| signal | its lines applied or it was rejected | `applied`; with tells (`tell:`, `outline:`, `pause:`, `resume:`) where each went, `; `-joined | `invalid` (it is `.bad`); an `open_refused` or `pick_failed` line |
| request | applied or failed (within a second of `request.md`); `pending` while an earlier request or its own files hold it | `committed <sha>`, `nothing to commit: every target is identical to HEAD (was the edited copy sent?)`, `applied; nothing changed` | `request_failed` (it is `.failed`; `targets missing: …`) |
| queue item | `enqueue`, `edited` | `queued as <id>; <start>` (and `; carries N parked edit(s) as things stand: …` when it would: `parked.md`; plus, if a watch or draw took its number meanwhile, `its number is also <id>'s: name it in full`); `rewritten: it runs the new text` / `its stage is redone`, plus `; now <start>` when the rewrite moved it | `invalid` (bad front-matter; a write after launch, set aside as `.md.bad`) |
| amendment | `amended` | `amendment #N, <state>: <delivery>`, then `delivered #N (<via>, after Ns)` | `amend_refused` |
| answer | `answer`, `late` | `answered: the executor has it` | `late` (the question had resolved) |
| proposed item | readable, new or changed (no event) | `proposed for <repo>: waits for Matt` | what `approve:` would refuse: an unknown `repo:`, item errors |

`<start>` is when the item will start and why, read from the scheduler's rules at queue
time (`state.start_estimate`; a guess, not a promise): `starts now`, `starts now, beside
028_x (alongside)`, `waits behind 024_x (repo busy, no `alongside`) and 025_y (template runs
alone)`, a lock, a `depends_on`, a pause, or `held: <reason>`. It is also the `starts`
field of the `enqueue` event (and of an `edited` one whose start moved).

A tell's receipt is never a bare `applied`: `message #N to <item>: it waits on question
<id>, …; <id> stays open` (its `ask` hands back for the message: `questions.md`; before 58,
`delivered as the answer to <question>`), or `message #N to <item>: delivered after
its next tool call`, whose `result` later becomes `delivered #N (<via>, after Ns)` when it
reaches the executor, or `not delivered: message #N: the executor exited first`. A queue
item rewritten while the daemon was down gets no new receipt.

## When

Every kind lands about a second after its write, so one combined stage of several
receipts finds them all (Word Warriors session 11 saw a proposal's and a request's
take ~6s). Between ticks (`poll_seconds`, 2) the daemon looks every 0.25s
(`WRITE_WAKE_SECONDS`) at the folders the planner writes into (`requests/`, `signals/`,
`proposed/`, `questions/`, `queue/<repo>/`, `backlog/<repo>/`) and ticks at once when
anything there is new or rewritten, or when a file still settling comes of age. A
queue item, proposal, backlog file or request is taken as soon as it reads (a file its
writer holds open does not); a signal or amend file, applied exactly once, also waits
until it has stopped changing: a `planner-*` signal 0.25s (`PLANNER_SIGNAL_SETTLE_SECONDS`;
the bridge writes it whole in one call), any other 1s (`SIGNAL_SETTLE_SECONDS`). The nap
ends when the first settling file comes of age, and is skipped when one came of age
during the tick: a signal written with a request is too young when the tick that
applies the request begins, and Word Warriors session 12 (Conductor 45) saw its receipt
a whole poll (~2.1s) after the request's. A tick's own length adds to all of them, so a
signal and a request written together get receipts within about a second of each other.
A launch, resume or stage takes seconds (a worktree's creation), so requests are also
handled just before and just after each one (`process_requests(between=True)`; one
targeting `conductor.toml` waits for the next tick): the bridge writes `request.md` after
the call's queue files, and on 2026-10-22 two requests written with queue items got their
receipts ~6s late, behind the item's launch (sandbox, the same order: 1.5s → 0.6–0.8s).

## Lifetime

Runtime state, never committed: `receipts/` holds a `.gitignore` of `*`, so a control
folder made before receipts existed needs no request; `conductor init` lists
`receipts/` in the tracked `.gitignore` too, and a request may not target it. Receipts
older than three days (`KEEP_SECONDS`) are pruned, checked at most hourly as receipts
are written.
