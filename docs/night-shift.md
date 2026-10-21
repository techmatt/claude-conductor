# The night shift

When Matt isn't watching (overnight above all), conductor notices stalls and drift and
keeps doing work Matt approved ahead of time, then sleeps. It never invents work and never
takes a decision that is Matt's. Code: `daemon_watch.py` (the daemon's side),
`nightshift.py` (the facts the inbox shares), `templates/watch.md`.

## The check (no tokens)

Every `watch_check_minutes` (default 10; 0 turns the check off, and the watch with it) the
daemon flags:

| flag | when | key (one event per key) |
|---|---|---|
| `quiet` | an executing item has posted no progress line for `watch_quiet_minutes` (20), since its last line or its start; not while it waits on a blocking question | item + its last progress event |
| `eta` | it is past its newest ETA: a progress `eta` or its outline's last row, whichever is newer; a clock time (`5:40pm`, `17:40`; on the day it was posted, the next day if that is over an hour before the posting) or a duration from the posting (`25m`, `1h30m`) | item + the progress event that posted it |
| `question` | a question open longer than one check interval | question id |
| `backlog` | a repo is idle (empty queue, nothing running) while its backlog has items that were not drawn; the text says why | repo + why |
| `memory` | the memory level is not `ok` | the `memory` event that set it |
| `run_failed` | a run target failed since the last check | that event |

Each flag is a `watch_flag` event (flag, key, text; item where there is one). The keys are
remembered from the log, so a restart flags nothing twice. Watch items are never flagged.

## The watch item

A short readonly run of `template: watch`. Conductor writes it as the next item of a repo's
queue (`queue/<repo>/NNN_watch.md`: `readonly`, `max_hours` = `watch_max_hours` (0.5),
`after_red: continue`, `stage: false`; its body is why it was launched) and logs a `watch`
event (item, reason, flags, file, by). It is launched:

- when a check raises a new `quiet` or `eta` flag (the stall's repo); the other flags are
  logged only, and the next watch sees them;
- about once an hour (`watch_every_minutes`, 60; 0: only on flags) while any item executes:
  an hour after the later of the last watch and the earliest start among those running;
- by the `watch: now` signal (**watch now**; `signals.md`).

Never two at once (one queued or running refuses another), nor during a reorder of its repo.
Never while a planner session is active: a planner signal (`by: planner`) or any request
within `watch_planner_minutes` (30) skips it, and the stall's `watch_flag` says `watch:
skipped: …`; the checks still run and log, and a skipped stall is tried again at each check
until a watch takes it or it ends. Not under memory pressure either. `watch: now` skips
those two rules. A watch never asks (its text says so); one blocked on a question anyway is
still stopped at its `max_hours`, unlike other items.

A watch starts at once beside anything, whatever runs: past an exclusive template, a red
hold or a `needs_planner` hold, in any repo, in its own worktree (a fresh checkout of main:
no running item's work in progress) with no setup. At launch conductor
inlines (`{{usage}}`) each executing item's prompt (quoted, first 6000 characters), outline,
last 12 progress lines and recent events, the open questions and the last six hours of
flags. It judges each item: a real stall or busy, still aimed at its prompt's goal or
drifting. Its report (short: **Items**, **Recommend**, **Actions**) never waits for review:
`View.reviewed` counts it reviewed, and the inbox's *Night shift* lists it.

**What it may do on its own**: its report's `## Actions` lines, carried out when it finishes
(not when red), at most six, attributed `by: watch`:
- `tell: <repo>/<NNN_name> <line>`: a `tell` (`progress.md`, *Messages*);
- `notify: <line>`: a toast (`notify` event);
- `draw: <repo>`: a backlog draw (the rules below still apply).

Anything else there (cancel, kill, amend, answer) is never carried out: a `warning` says
so. It recommends those in its report, for Matt or the planner.

## The backlog

`backlog/<repo>/<name>.md`: items Matt approved ahead of time, written only by the planner
on his word, with normal front-matter (`items.md`). The name needs no number (`bug_sweep.md`;
`2_bug_sweep.md` orders it); they are taken in name order, numbers as numbers. The folder
ignores itself (`backlog/.gitignore`), as `receipts/` does. Each new or rewritten file gets a
receipt, `receipts/backlog/<repo>/<name>.md`: accepted (waiting), or refused with what would
make it invalid in the queue; a draw rewrites it (`drawn into the queue as <id>`).

**Draw.** Every tick, a repo whose queue is empty, with nothing but a watch running, not held
(red, `needs_planner`) and not mid-reorder, draws its first eligible item: moved to
`queue/<repo>/NNN_<name>.md`, NNN one past every number queued, running, done or proposed there, with a
`backlog_draw` event (from, full_length, night, by), then the usual `enqueue`. Day or night.
Eligible: valid, every `depends_on` done, and:

- **Never two full-length items back to back without Matt in between.** Full-length: any
  template run but a watch, or a declared `max_hours` of 2 or more (only a declared one: the
  usual default `max_hours` is 2). A full-length item is drawn only if, since the repo's last
  full-length item started (queued by hand or drawn), Matt or the planner wrote a signal (the
  app's count), a request, or an approval. A short item behind it is drawn meanwhile.
- **The nightly budget.** At night (`night_start_hour` 18 to `night_end_hour` 8, local
  wall-clock time), backlog items' executor-hours inside the night (each drawn item's start
  to its finish, or now) are capped at `backlog_night_hours` (6): an item is drawn only if
  its whole `max_hours` (its own, else the default) fits in what is left, so a shorter one
  behind it may still go. Once spent, or nothing fits, nothing more is drawn that night.

At night the daemon logs `backlog_sleep` once per kind (key `sleep:<date>:<kind>`, reason,
until, spent_hours, budget_hours): `budget`, no more draws tonight; `idle`, every queue empty
with nothing eligible, a note only (a backlog item written later is still drawn).

A draw takes only files settled and receipted, never one still being written; it is
retried only when the log, the backlog or day/night changed since the last try.

## Where it shows

The inbox and the session digest have a **Night shift** section: each repo's backlog
(invalid and full-length marked), tonight's budget used and whether it sleeps, and since the
read mark the draws, flags, watch launches and watch reports.

## Settings

`[defaults]` in `conductor.toml`: `watch_check_minutes` 10, `watch_quiet_minutes` 20,
`watch_every_minutes` 60, `watch_planner_minutes` 30, `watch_max_hours` 0.5,
`backlog_night_hours` 6, `night_start_hour` 18, `night_end_hour` 8.
