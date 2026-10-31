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
| `quiet` | an executing item has posted no progress line for `watch_quiet_minutes` (20), since its last line or its start; not while it waits on a blocking question, nor while a process of its own is still writing (`memory.md`, *Writes*: found live, it fired twice on aicomplete 009 as its experiment log advanced; sampling off, it fires as before), nor while its outline's next row or latest `eta` is still ahead, plus 5 minutes (`QUIET_GRACE`; 048 was flagged at 3:43am with a row due 3:50am). A far-off `eta` holds it that long; the routine watch still looks in | item + its last progress event |
| `eta` | it is past its newest ETA: a progress `eta` or its outline's last row, whichever is newer; a clock time (`5:40pm`, `17:40`; on the day it was posted, the next day if that is over an hour before the posting) or a duration from the posting (`25m`, `1h30m`) | item + the progress event that posted it |
| `question` | a question open longer than one check interval | question id |
| `blocked` | a blocking question unanswered for `watch_blocked_minutes` (15): its item does nothing meanwhile | `blocked:` + question id |
| `backlog` | a repo is idle (empty queue, nothing running) while its backlog has items that were not drawn; the text says why | repo + why |
| `memory` | the memory level is not `ok` | the `memory` event that set it |
| `run_failed` | a run target failed since the last check | that event |

Each flag is a `watch_flag` event (flag, key, text; item where there is one). The keys are
remembered from the log, so a restart flags nothing twice. Watch items are never flagged,
nor a paused repo's items or questions.

## The watch item

A short readonly run of `template: watch`. Conductor writes it into a repo's queue under a
series of its own (`queue/<repo>/W<n>_watch.md`, n one past the repo's last watch), so it never
takes or moves a queue number; older `NNN_watch` items keep their names, and a `W<n>_` name on
anything but a watch is invalid. It is `readonly`, `max_hours` = `watch_max_hours` (0.5),
`after_red: continue`, `stage: false`; its body is why it was launched. Conductor logs a
`watch` event (item, reason, flags, file, by). It is launched:

- when a check raises a new `quiet`, `eta` or `blocked` flag (the stall's repo); the other
  flags are logged only, and the next watch sees them;
- every half hour (`watch_every_minutes`, 30; 0: only on flags) while any item executes:
  that long after the later of the last watch and the earliest start among those running
  (a paused item doesn't count). No item running, or only paused ones: no watch;
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
(not when red), at most six, attributed `by: watch` (`none` and the closing `Report written
to …` line, which sits under `## Actions` as the last heading, are not actions):
- `tell: <repo>/<NNN_name> <line>`: a `tell` (`progress.md`, *Messages*; it never answers a
  question: `questions.md`);
- `answer: <question id> <answer> (source: <where>)`: answers an open question **only** with
  an answer Matt or the planner already gave (a message, a ruling in the item's prompt or
  an amendment), naming it; the answer file says so, and `watch_answer` (question, text,
  source, watch) logs it. Refused without `(source: …)`, or for a question not open or
  already answered. Conductor can't check the source: the template forbids anything new;
- `notify: <line>`: a toast (`notify` event);
- `draw: <repo>`: a backlog draw (the rules below still apply).

Anything else there (cancel, kill, amend) is never carried out: a `warning` says so. It
recommends those, and any answer nobody has given yet, in its report.

## Autopilot

One switch per project for whether the night shift may start work on its own: **off**,
**night** (only inside the night window, `night_start_hour` to `night_end_hour`) or
**always** (the default, and the behaviour before it existed). The mode is the last
`autopilot` event's (set by the `autopilot:` signal from the app's header or the planner),
so it survives restarts and needs no tracked file. It gates only what starts work: outside
what it allows there are no backlog draws, a watch's `draw:` included (refused with a
`warning`: "recommended only"). Repair is not starting work: a watch's `tell:` and
`answer:`, its `notify:`, the checks and the watch itself run at any setting (Word Warriors
125, 2026-10-22: a correct diagnosis every hour from 7:20pm, its fix only recommended, the
item idle until 11:55pm). A repo's `backlog` flag then says `autopilot is off` (or night only).
`nightshift.plan` says in a few words what it will start next (`next: bug_sweep when the
queue empties`, `next: bug_sweep from 6pm`, `held: back-to-back`, `held: budget`): the app's
header shows it beside the mode, and the inbox's *Night shift* opens with the mode.

## Pause

`pause: <repo>` (**pause R**; the app's **Pause**) is for when Matt needs the box (playing
the game, say). The daemon logs `pause` (repo, items, by), stamps each item executing or
setting up there (watches aside) `paused_at` in its run record, and sends each conductor's
pause text as a tell (`pause: true`; an item waiting on a question gets it when its `ask` hands back, `questions.md`):
commit, stop every browser, test worker and server it started, then call `wait`
(`mcp__conductor__wait`, which returns when a message waits for it or after up to 20
minutes) again and again, never asking a question. Nothing in the repo launches or stages
until `resume:` (the head's decision is a `wait`, `paused …`, never a toasted `hold`; no
early start either), and no draw or watch starts for it. A paused item is never killed at
its deadline, never flagged and never counted toward the routine look.

`resume: <repo>` (**resume R**) logs `resume` (items, paused_seconds), moves each paused
item's deadline later by its pause (`paused_seconds` in its run record), and sends the
resume text (`resume: true`): the new stop time, and to move its own end times (a bug
hunt's) by as much. A pause outlives sessions and restarts (`View.paused`: the last
`pause` with no `resume` after it): the inbox's *Now* line says `**PAUSED** since …` and
the app's header shows it, until resumed. Pausing a paused repo, or resuming one that
isn't, is refused.

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
  usual default `max_hours` is 2). A full-length item is drawn only if Matt acted since the
  repo's last full-length item started (queued by hand or drawn): a signal file `by: app`
  or `by: cli` (any line), or a planner write that carries his approval: `approve:`, a queue
  write (`enqueue` or `edited`; never a draw's or a watch's), an answer, or a backlog file
  written since. A request (`state.md` above all) and any other planner signal (`review:`,
  `reviewed:`, `tell:`, `checkpoint:` …) are bookkeeping and don't count
  (`nightshift.matt_since`). A short item behind it is drawn meanwhile.
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

The inbox and the session digest have a **Night shift** section: the autopilot mode, each repo's backlog
(invalid and full-length marked), tonight's budget used and whether it sleeps, and since the
read mark the draws, flags, the watch's tells and answers, and one count line for routine
watches (launches and `done` reports; any other report is listed): `inbox.md`.

## Settings

`[defaults]` in `conductor.toml`: `watch_check_minutes` 10, `watch_quiet_minutes` 20,
`watch_every_minutes` 30, `watch_planner_minutes` 30, `watch_blocked_minutes` 15, `watch_max_hours` 0.5,
`backlog_night_hours` 6, `night_start_hour` 18, `night_end_hour` 8.
