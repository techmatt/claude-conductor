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
  A signal refused part-way also has `applied`: the lines that applied before the one
  that failed (they stay applied).
- `events`: the ids of every event its handling logged, one line each below: a
  signal's, side effects included; a request's `request`, `commit` (and a `closeout:`
  request's `read`). Past eight (`LINES`; a `session:` warns once per unreviewed
  report) the body ends `… and N more`.
- `written_ms`: the write's mtime in ms when conductor took it (absent for a signal or
  request, gone by then). A rewritten queue or proposed item overwrites its receipt:
  one whose `written_ms` is older than the rewrite is the previous version's.

| write | written when | result | refused when |
|---|---|---|---|
| signal | its lines applied or it was rejected | `applied` | `invalid` (it is `.bad`); an `open_refused` or `pick_failed` line |
| request | applied or failed; nothing while it waits for targets | `committed <sha>`, `applied; nothing changed` | `request_failed` (it is `.failed`) |
| queue item | `enqueue`, `edited` | `queued`; `rewritten: it runs the new text` / `its stage is redone` | `invalid` (bad front-matter; a write after launch, set aside as `.md.bad`) |
| amendment | `amended` | `amendment #N, <state>: <delivery>`, then `delivered #N (<via>, after Ns)` | `amend_refused` |
| answer | `answer`, `late` | `answered: the executor has it` | `late` (the question had resolved) |
| proposed item | settled, new or changed (no event) | `proposed for <repo>: waits for Matt` | what `approve:` would refuse: an unknown `repo:`, item errors |

A `tell:` or `outline:` signal's receipt likewise gains a `delivered #N` line when the
message reaches the executor. A queue item rewritten while the daemon was down gets no
new receipt.

## Lifetime

Runtime state, never committed: `receipts/` holds a `.gitignore` of `*`, so a control
folder made before receipts existed needs no request; `conductor init` lists
`receipts/` in the tracked `.gitignore` too, and a request may not target it. Receipts
older than three days (`KEEP_SECONDS`) are pruned, checked at most hourly as receipts
are written.
