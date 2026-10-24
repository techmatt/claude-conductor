# Checkpoints, the session digest, the review bundle, the inbox and toasts

## Checkpoints

A checkpoint is a session boundary, so each planner session starts with a clean view.
`checkpoint: <label>` (the planner's `session:` below writes one; the app's **New
checkpoint…**; `conductor checkpoint [label]`, `by: cli`) logs a
`checkpoint` event (label, by). **Its number is its event id**: nobody keeps a counter.
Only the latest checkpoint matters to the views.

- **Hidden:** a finished item whose `finish` event and first `reviewed` event both come
  before the latest checkpoint, unless its red still holds its queue (`clear:` not yet
  sent). Such an item is *settled*.
- **Carried forward, whatever their age:** unreviewed reports, an item reviewed only
  after the checkpoint (until the next one), red holds, open questions, and anything
  queued, staged, running or proposed. None of these is a finished-and-reviewed item,
  so none is ever hidden.
- **Warning:** at the checkpoint, every report still unreviewed gets a `warning` naming
  it (item, `checkpoint`, `finish`, `note`), so the new session raises it first.

Nothing is deleted or moved: reports, `done/` and `events.db` stay as they are, and
`conductor events`, a postmortem, the app's trail and `conductor open` reach any item.
What changes is only the default views:
- `inbox.md` names the current checkpoint (number, label, time, by) under its header
  and how many settled items it hides; an unreviewed report older than the checkpoint
  is tagged *carried from before checkpoint #N*.
- The app's Repos tab lists only current finished items, with the settled ones in a
  collapsed *earlier checkpoints* group per repo. The Events tab starts at the latest
  checkpoint (*since checkpoint #N*, on by default; off shows the whole log).
- `conductor events --since checkpoint` (or `checkpoint N`) starts at a checkpoint;
  `conductor status` names the latest one.

## Session start digest

`session: <label>` (planner, `session: start` with `by: planner`; `conductor session
[label]`, `by: cli`, label default `start`) opens a planner session in one signal, so
the planner reads one file instead of round-tripping a checkpoint, a re-stage and a
`read:`. In order (`session.py`):

1. a checkpoint with that label, as above, its event carrying `session: session.md`
   (the unreviewed-report warnings included);
2. `session.md` in the control folder (runtime, git-ignored), overwritten: its header
   names the signal file it answers and the checkpoint (number, label, time), so a
   planner that staged it too early sees it is the previous one and stages again. The
   body is the inbox exactly as it stood after the checkpoint (questions, unreviewed
   reports with conductor's own among them, picks, every unsettled event since the
   previous read mark, *Now*), then:
   - **From state.md**: the control folder's `state.md` lines a start raises, copied as
     written, so the planner need not stage it: *Waiting on Matt* and *Open questions*
     whole, and *Pending fixes, due* and *Maintenance, due*: each top-level bullet (with
     its indented lines) of that section saying the word `due`, any case. Sections match
     by `## ` heading, ignoring a `(…)` after the name; a missing one says so, as does a
     missing `state.md`;
   - **Maintenance facts**, per repo, with no judgement: for each template (bug hunt,
     general cleanup, cleanup, test cleanup, claude.md cleanup, disk space audit; an item run under an old name, `general_audit` or `test_perf`, counts as the new one) the
     last finished item that ran it, when and its status, or *never*, the number of
     non-template items finished since (or in all), and any in flight; the repo's
     `CLAUDE.md` size (KB and lines, from the main checkout); its unused audit notes;
     and each `slow:` component (the words before ` ~` or the first comma) named in two
     or more of its last five finished reports, with their numbers;
3. a `read` event through the last event the digest shows (detail `session`: the
   checkpoint's id), so the next inbox and the next digest start after it.

The digest stays as written until the next `session:`; `inbox.md` carries on as
before. The planner decides what to recommend from the facts.

## Review bundle

`review: all` (or `review: <ref>`, `signals.md`) writes `review.md` (runtime,
git-ignored, rewritten whole) so the planner reviews in one read (`review.py`). It
marks nothing reviewed. Layout:

1. `# Review — <project>`, then ``Written for `signals/<file>` at <clock>: `review: all`.``
   (or the item ref) and a line saying nothing is marked reviewed: like `session.md`,
   a stage that names an older signal is the previous bundle.
2. `## Unreviewed reports` (`## Report` for one item): the inbox's one-line summaries
   (`inbox.unreviewed_lines`), or `- none`.
3. Per report, oldest first: `## <repo>/<NNN_name> — **<status>**, finished <clock>
   (#<id>)` (plus ` · reviewed #N` if it already is), ``- report: `reports/…md` ``, a
   red item's `- red: <reasons>`, then `- amendments:`, `- audit notes:` and `- setup
   log:` paths only when those files exist (never their contents), then the report
   inlined whole and raw. A missing report is one line (`- the report file is missing: …`).
4. `all` only: `## Conductor reports`: the marker line from conductor's `planner-notes.md`
   (or `: none.`), then each of conductor's reports past it (*Conductor reports*, under
   *Inbox*), as `### <name>`, its absolute path and its text
   inlined whole. No `planner-notes.md`, or no marker line in it (a leading `- ` or `**` is
   fine): one line saying so, nothing listed.

## Inbox

`inbox.md` is regenerated whenever the log or the read mark changes (and every 30s
for the *Now* section), as a whole-file replace through `<file>.<pid>.tmp`, so a reader
sees one render or the next, never a splice; each heading and event line appears once.
Its header names the current checkpoint (see *Checkpoints*).
First **Questions**: every open ask, regardless of the read
mark. Then **Unreviewed reports**: every finished item without a `reviewed` event,
oldest first, with its status (a red one's reasons after it), finish time, finish event id, report path and `carry:`
line (and ` · slow: …` unless its report said `slow: none` or nothing, and
` · pick: <ref>` when its report named a picker, then ` · **picks saved <time>**` once its
choices are saved (`picks.saved_at`; `session.md` and `review.md` too, so a planner reads
the choices instead of asking); ` · resume of <orig>` for a resume
item, `resume.md`); it shows on every inbox, read mark or not, until a `reviewed:`
signal names the item. After them, **conductor's own reports** past the planner's
marker (*Conductor reports* below), one line each: ``- conductor's <name> **<status>** —
`<absolute path>` `` (the value of its `status:` line, or *no status line*), until the
planner moves the marker; no notes file or marker line lists none.

**Conductor reports** (`session.reports_past_marker`): the `reports/<name>_report.md`
files in conductor's own checkout (found from the installed package, `src/conductor/` →
the repo root) past the `Last report reviewed by the planner: <name>` line (a leading
`- ` or `**` is fine) in its git-ignored `planner-notes.md` (the planner's file). A marker
that `prompts/ledger.md` names in a `<time> done <name> → …` line, a prompt or a cleanup
pass, orders by those lines: every report whose `done` comes after it. Any other
numbered marker orders by natural order (12 < 12a < 13); an unnumbered one the ledger
lacks has nothing past it.

**Reviewed reports that change.** A reviewed report, or the `.audit.md` beside it,
edited afterwards (a session resumed by hand through **Terminal** can write to it) is
unreviewed again: `report_changed` (`signals.md`, *reviewed*) puts it back in
*Unreviewed reports*, `session.md` and `review.md` with
` · **report changed since review <time> (after it was opened by <by>, #<id>)**`
(the files changed since the review; the opened part when a `session_opened` came
after the item finished), until a new `reviewed:`. A file that can't be read (held,
mid-save) is looked at again next scan, never taken as a change. Then **Picks**, also
standing (see `picks.md`, *Pending*): each pending picker, then any picker saved since the
checkpoint (any time without one) that no pending item names, as the saved line
without the item:

```
- art_alts_009 saved 4:52pm: 6 changed, 2 notes; `picks/art_alts_009.choices.json` (code/012_art_alts)
- code/013_more opened 4:40pm, not saved — `code/picks/more.json`
- code/014_faces waiting for Matt — signal `pick: code/picks/faces.json`
```

Then **Night shift** (`night-shift.md`): each repo's backlog, tonight's budget and whether
it sleeps, and since the mark the draws, flags, the watch's tells and answers, and its
reports (which never wait in *Unreviewed reports*); their events leave *Other*. Routine
watches, a launch and a `done` report, are one count line (`3 watches (9:02pm–6:40am), 1
tells/answers; latest report <path>`); any other watch report is listed.

Then every event after the read mark that is not *settled* (`inbox.settled_why`), so
the digest shows what is live (Word Warriors session 12's start: 263 events, ~60%
settled). Settled: every `push`; a checkpoint's still-unreviewed `warning` (*Unreviewed
reports* lists the item); an event of an item whose report is reviewed (a red
kind aside); anything before the newest request among them that rewrote `state.md` (a
`closeout:` one whatever its targets), reds included, since the planner wrote down what
it made of them, bar an unreviewed `finish` and a hold still in effect; and a rejected
signal (`invalid` of a `signals/` file) once acknowledged (`ack: invalid`) or before the
latest checkpoint (a session start's included), whatever else applies to it. The header
counts each reason: `· hidden as settled: 23 pushes, 132 of reviewed items, 99 passed over
by `state.md` request #2917, …`. A control commit is folded into the request it
committed: one line, `… · committed <sha> (#<id>)`. A request whose message starts `closeout:`
also logs a `read` event through the newest event (detail `closeout`: the request
folder's name), so the next inbox and digest start after the ended session. The rest are
grouped **Red** (tamper first,
then the rest in event order: `red`, `conflict`, `paths_violation`, `kill`, `request_failed`, `invalid`, `run_failed`,
`pick_failed`, `amend_refused`, `memory_critical`, `daemon_died`),
**Holds**, **Finished** (status, blocks, carry, pick, report path), **Questions resolved**
(answers, defaults taken, late answers, closes), **Requests and commits** (merges
included), **Other**; then **Now**: first the memory line (`memory.line`: commit of
limit, percent, free GiB, the biggest item from the daemon's latest sample, and the
level, bold with *launches and stages paused* when not ok), then per repo, what is running (or merging), staged
(`staging`/`staged`), queued, waiting or held, and *audit pending* if notes are unused,
ending ` · next item NNN` (`nightshift.queue_name`: one past every number queued,
running, done or proposed there; a planner had no other way but listing `reports\`),
plus proposed items. A hold stays in
the Holds section until the item starts, merges, finishes or is cancelled, or a
reorder renames it (its old id is gone; the renamed item gets its own hold). `enqueue`, `start`,
`stage` and `worktree` events are left out of the inbox (they are in
`conductor events`). `conductor status` prints the daemon (running, **not answering**
with a stale heartbeat, **died**, or not running) and the same memory line, then per repo: running (pid, branch,
`readonly` or `resume of <orig>`, port, kill time), merging (with the reason it waits), staging/staged, queued, and the
*audit pending* line when the repo has unused audit notes.
`conductor inbox` prints the same thing without moving the mark.

## Toasts

The daemon raises a Windows toast (library: `windows-toasts`, WinRT in-process) for
`ask`, `red`, `tamper`, `hold`, `run_failed`, `pick_failed`, `amend_refused`, `memory` (a level other than
`ok`), `memory_critical`, `daemon_died`, `notify` (a `notify:` signal or a watch's action) and `queue_empty` events. Each title names the project and
the item; the toast's tag is `<project>#<event id>`, which is what the Action Center
history records. Toasts use Command Prompt's registered app id, with "Conductor" as
the attribution line, so nothing has to be installed. A toast that fails is logged as a
`warning` and never stops the daemon. `toasts = false` in `[defaults]` turns them off;
on other platforms each would fail, so turn them off there.

`queue_empty` is logged when an item finishes and its repo has nothing queued or
running.
