# Workflow retros

A workflow retro is a self-audit (`template: self_audit` on a resume item, `templates.md`,
`resume.md`) of one finished item: its own session, forked, looks back over its run into
notes for the repo's next cleanup. Conductor queues one itself when one is due
(`daemon_retros.py`, 107); before 107 the digest marked it **due** and waited for the
planner to offer **post-audit X** and Matt to say yes (aicomplete 192 finished `partial` at
9:45pm; its retro ran at 10:49am the next day, in about 2 minutes once queued).

## When one is due

`session.retros` counts the repo's non-template, non-resume items finished since the newest
self-audit was queued (its `retro` or `enqueue` event; its finish when it has neither, so
what finishes while one runs counts toward the next). One is due when any of them:

- went `partial` or `red`, unless every reason of its red is an API error or a failed
  launch (111, `api-retry.md`: such an item is never the one picked either), or
- ran past 1.5× its first ETA and 10 minutes over (`RETRO_OVERRUN`, measured from its
  `start` to the first `progress` with an `eta` or outline), or
- at 12 of them with none of the above (`RETRO_FLOOR`).

It names the item with the most friction among those that triggered it (at the floor, all
of them): its `friction:` parts, watch tells to it, questions that timed out to their
default, watch `eta` flags, and red (counts double); ties go to the latest. Never an item a
self-audit (queued, running or finished, conductor's or Matt's) already covers, nor one
with no session to fork (no `session_id`, a `launch failed` red, or a transcript gone from
Claude Code's projects folder: `resume.md`). Template items
(watches included) and resume items (**ask X**, self-audits) are never counted.

## Queued by conductor

Each tick after a `finish`, `pause`, `resume` or `retro` event, for each repo:

- skipped while the repo is paused (`pause: <repo>`) or a reorder of it is being finished,
  or while a self-audit is already queued or running there (one at a time per repo);
- otherwise the due one is written to `queue/<repo>/<NNN>_post_audit_<X's number>.md`,
  numbered like any item (`nightshift.queue_name`), front-matter `resume: <repo>/<X>`,
  `template: self_audit`, no body, and logged as a `retro` event (`of`, `why`,
  `by: conductor`). It fixes the obvious in place and lands like any serial item (113,
  `resume.md`), so a red's retro waits behind its hold until `clear:`.
- **A cancel sticks** (115): `cancel:` of a retro conductor queued records `retro_of` on
  its `cancel` event, its item counts as covered and the cancel as a marker, so it is never
  queued again and nothing that finished before it brings one back; a later trigger still
  gets its own. (aicomplete: cancelling 220_post_audit_215 re-queued it as
  222_post_audit_215, which shared 222 with 222_sim_players; a new item no longer takes a
  number in use either: `items.md`.)

It is not a night-shift draw: autopilot does not gate it, nor does the time of day.
`[defaults] auto_retros = false` in `conductor.toml` turns it off; then the digest marks
it **due** and the planner offers **post-audit X** as before. **post-audit X** stays for
retros Matt asks for by hand (`planner-vocab.md`).

## In the digest

`session.md`'s *Maintenance facts* end each repo with a **workflow retro** line: the last
self-audit that ran (item, time), then `queued: <item>` or `running: <item>`, then the
count since. A due one reads `next: post-audit <X> (why)` with when it queues: `queued once
<item> ends`, `queued when <repo> resumes`, or `conductor queues it` (the tick before the
digest had not yet run). With `auto_retros = false` it reads `**due**: post-audit <X> (why)`.
The inbox shows the `retro` event as `workflow retro <item> queued: post-audit of <X> (why)`.

## For the planner

Process a retro's report as any self-audit's: **About conductor** to `planner-notes.md`,
project findings to `state.md`, then prune *Planner habits*. Never offer **post-audit** for
a retro conductor queues.
