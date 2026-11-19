# Audits and templates

A template ends with the leftover-budget line (`executor.LEFTOVER`, `preambles.md`), but not `watch` or a readonly one; `self_audit` gets `LEFTOVER_AUDIT`: optional, a further check's outputs in scratch, inside the load budget (047's speed pair ran all 12 threads).

Code-quality audits are conductor-owned texts, not prompts the planner retypes. They
live in `src/conductor/templates/` (`cleanup.md`, `general_cleanup.md`, `test_cleanup.md`,
`disk_cleanup.md`, `claude_md_cleanup.md`, `content_cleanup.md`, `bug_hunt.md`, `self_audit.md`, `watch.md`; `audit.md` is the *Audit notes*
paragraph, not a template) and are versioned with this repo: the `start` event of a template item
carries `template` and `template_version` (the text's sha256, first 12 hex digits), so
`git log` on the file finds the exact text sent. Executors never see the control
folder, so whatever a template needs is inlined into the prompt (kept, as always, in
`.prompt.md`). The texts use `{{name}}` placeholders (`repo`, `project`, `notes`, `usage`,
`original`; `audit.md`'s `notes_path`). A template item gets the full preamble, a watch its own
(`preambles.md`), and the template's text follows it unchanged.

**Which one a `slow:` cause picks** (the planner, at start; moved from `planner.md` in 111): a
cause named in 2–3 reports: tests → test cleanup; searching → claude.md cleanup; a build or a
failing tool → general cleanup.

**Names.** *Audit* is kept for passes whose job is notes (`audit: true`, **post-audit**,
`self_audit`), though since 113 they fix the obvious in place too; one whose job is to edit, delete or move is a *cleanup*. So `general_audit` became
`general_cleanup` and `test_perf` `test_cleanup` (prompt 36), `disk_audit` `disk_cleanup` and
`content_audit` `content_cleanup` (prompt 54); the old names are aliases (`audits.ALIASES`): an item
queued under one runs the new text and is recorded under the new name, and an event or run
record carrying an old one still counts (maintenance facts, exclusivity, notes). A project's
`state.md` *Maintenance* may still say "disk space audit": the planner's own file, renamed at
its next write.

Every template but `bug_hunt`, `self_audit` and `watch` is
*exclusive* (`audits.EXCLUSIVE`): it runs alone in its repo, and a template item with
`alongside` is invalid (`scheduling.md`, *Exclusive templates*).

- **`audit: true`** (any item; explicit, never automatic). The preamble gains an
  *Audit notes* paragraph (`audit.md`) naming an absolute path,
  `reports/<repo>__NNN_name.audit.md`, as the second file the executor may write
  outside its working directory. It keeps a running notes file there while it works,
  covering every task, under six headings: what was poorly structured, what took
  longer than expected and why (tokens and context as well as time, by activity), what
  was *re-derived or re-read* (the same file, search or fact established twice) and what
  would have cached it (108: retired Matt's hand-run session retrospective; a cleanup adds
  that doc line, index or script), what should be refactored or cleaned up, what
  CLAUDE.md or README should say, and (84) what *needs a decision*, with options and a
  pick: those bullets ride the report's inbox line (`audits.decisions`, `inbox.md`) so the
  planner rules on them at review, where cleanups had deferred them back as "planner's
  call" (aicomplete 123 deferred 2, 129 deferred 6). Nobody reviews the other notes per item. Before finishing it
  implements each obvious note (a small, safe fix, a doc line), leaving notes only for a
  decision or a large change, and its report lists those it implemented (`executor.AUDIT_FIX`;
  75, Matt: less churn for the cleanup; not in a readonly or template item). Since 113 every
  writable item fixes the obvious in place (`preambles.md`); this line only adds the list. The item's `finish`
  event carries `audit: true` and lists the notes file among its paths. Once running,
  the `audit:` signal sends the same paragraph as a message instead (`signals.md`).
- **`template: cleanup`**. The prompt after the preamble is `cleanup.md` with every
  *unused* note of the repo inlined, oldest first, each headed `### Notes from <item>,
  finished <status> <time>`, **deduped** (122, `audits.render_notes`; general cleanups too): a
  bullet raising what one of the repo's 30 newest *used* notes raised is left out (a count
  line says so), so no cleanup re-raises what an earlier one handled; a bullet repeating an
  earlier one in the batch is merged into it, tagged `raised again by <items>: one problem;
  fix it everywhere`, and the template has the cleanup report it as one entry naming the
  class. "The same" is whole-word overlap (Jaccard ≥ 0.6, exact under 4 words), so a
  heavy rewording can slip through; then, after `---` and "The planner adds, for this item:",
  the item's own body, if any. The executor carries out what is clear and well
  defined and defers every design-level call (its report lists them under
  **Deferred**, with options and a recommendation, any that is Matt's own, by taste, scope or a
  rules term, marked `(Matt's call)`, as in every template with a **Deferred**: `planner.md`). A cleanup never stages.
- **`template: general_cleanup`** (was `general_audit`). The same, with its own audit first: it behaves as
  `audit: true` (it writes its own notes file), audits the whole repo, then carries out
  the clear items and defers the design calls. It also takes every unused note at launch and
  inlines them as a cleanup does (prompt 69), but with none it simply runs. It never stages.
- **`template: test_cleanup`** (was `test_perf`). Set up or tighten a fast/slow lane split and speed tests
  up generally; a small loss of thoroughness or moving a slow test to the slow lane is
  allowed; the report gives before/after timings per lane (the one report where test
  detail belongs). It uses no notes.
- **`template: disk_cleanup`** (was `disk_audit`). Find why the project takes so much disk and free what
  can safely go, under the project's storage policy (`claude-ai-new-project.md`, *Storage*):
  delete what is cheaply rebuilt or clearly obsolete, move what is expensive or
  irreplaceable but not needed day to day into `archive\` under the project's Drive
  home, never delete anything hand-made, and only move, never delete, anything in a Drive home; history rewrites and other destructive calls
  go under **Deferred**. The text overrides the preamble's main-checkout rule only for
  deleting untracked, cheaply rebuilt files there, and requires links (Drive junctions,
  `shared` links) to be removed as links before any recursive delete. At launch,
  conductor retries the repo's leftover worktree removals, then inlines what only it
  sees (the `{{usage}}` placeholder): the main checkout and its `shared` folders; the
  control folder's sizes by location (run logs, with the repo's share and the five
  largest, the rest of `reports/`, `events.db`, `.git/`, `.conductor/`, `done/`,
  everything else; `audits.control_usage`, a walk that never follows links); and each
  folder under the repo's worktree root, marked live (never touch), a leftover removal
  conductor retries, a red item's kept worktree, or stray. The executor never deletes a
  worktree folder itself. The report gives a before/after size table by location. A
  disk cleanup never stages.
- **`template: claude_md_cleanup`**. A quick pass through the repo's `CLAUDE.md`:
  cut what is extraneous or duplicated (a README fact gets a pointer, not a copy), fix
  what is wrong against the code, tighten the wording, keep the standing contract the
  new-project doc requires. Changes to how executors work go under **Deferred**. The
  report is short: **Cut**, **Fixed**, **Deferred**.
- **`template: content_cleanup`** (prompt 49, was `content_audit`; **content cleanup R**, on Matt's word only, never a
  *Maintenance* default). The content its audience meets, checked against who the
  project's docs say that is: player-facing content by default, or docs when the body names
  them (118: aicomplete 239, a docs trim, had to map player wording onto docs). What is
  ambiguous, too rare or wrong for that audience (in docs, also stale, duplicated or
  misplaced) is stored (out of play), moved, cut, reworded or flagged, each *set* (a pool,
  a kind of line, a doc or section) kept workable. Design-level calls go under **Deferred**.
  The report has **Moved** (by set, each with a reason), **Pools** (each set's size before
  and after) and **Deferred**. It edits, so it is exclusive.
- **`template: bug_hunt`**. Run the project as its intended users would (whoever its
  docs say they are), find what is broken, confusing, ugly or slow, fix the clear bugs
  and list the rest. The method: build or extend a reusable checker kept in the repo
  and documented in its README (drives the app across many seeds and paths at speed,
  screenshots only at meaningful moments, automatic checks there: overflowing text,
  overlaps and hidden targets, missing or placeholder assets, console errors, missing
  audio, input dead spots, frame-time spikes, memory growth; a debug hook or flag
  where that is cheap); review contact sheets and use the app at real speed; fix clear
  bugs and make each automatable check a test; keep a findings list in the repo
  (`docs/qa-findings.md` unless it has a convention), read first so nothing known is
  re-reported. **Scope and stop on yield** (137; Matt's "a budget is a target" no
  longer applies to hunts): **bug hunt** is the recent-changes one: the git log since
  the last hunt, each changed area driven hard, plus the checker sweep; another lens
  (paths not yet driven, odd input, long runs, a content sweep, code reading, the
  checker itself) only where a round points at it. **Full bug hunt** (the body asks for
  it) gives every lens one real round first. A round is *thin* when it found nothing a
  user would hit, only wording, stale lines or polish; two thin rounds in a row end the
  hunt. `max_hours` (the vocab's N + 1: an hour for setup, merge, report) is a cap; no
  *Leftover budget* line. The body usually names a focus. With `readonly: true` it is
  *notes only*: find and record but change nothing tracked (checker and screenshots in
  a `shared` folder or nowhere, findings in the report only). The report has **Time**
  (start, scope, end and why: `stopped on yield` or `at the cap`, one line per round
  with what it found), **Checker**, **Fixed** (notes only: **Found**) and **Deferred**,
  most important first, each with options and the executor's pick. Ending early is never
  `partial`; only clear bugs left unfixed for lack of time are.
- **`template: self_audit`** (only on a resume item, `resume.md`; **post-audit X**). The
  original's own session, forked, looks back over its run, from memory and its record,
  and fixes what is obvious in place before finishing (113: stale docs, small bugs, dead
  code, its own or the run's findings; it commits and lands, `resume.md`), noting only what
  needs Matt's decision or is large. It behaves as `audit: true`: its notes, at the resume item's own
  `.audit.md` path, start `Self-audit of <orig>.` and use the five non-decision headings plus
  *What happened vs. what was asked* (each divergence and when it started), *What
  misled you* (quoted, with the wording that would have worked, then concrete workflow
  changes, each with what it would have saved: 59) and *Where the time went*, writing only
  what the original's own notes miss or got wrong (75: aicomplete 089 and 095 repeated
  theirs, and the cleanup verified them twice). It runs as the
  **workflow retro**, which conductor queues itself when one is due (`retros.md`). They count as unused notes for the repo's next cleanup, whose inlined heading
  names the original (`… : a self-audit of <orig>, written by its own session`). The
  report has **Summary** and **About conductor** (findings about the template,
  preamble, planner or tools, for the planner to carry to conductor's `planner-notes.md`). Not
  exclusive, but serial like any writable item (113; `readonly: true` makes it a look that
  runs beside anything).
- **`template: watch`** (conductor writes these; `night-shift.md`). A short readonly look
  at the running items: a real stall or busy, on goal or drifting. Its `{{usage}}` is each
  executing item's prompt (quoted), outline, progress and recent events, the open questions
  and recent flags; its body says why it was launched. Its `## Actions` lines (tell, notify,
  draw, answer) are carried out when it finishes; it never cancels, kills or amends.

**Which notes are unused.** A note is a finished item whose `finish` event has
`audit: true` (an `audit: true`, general cleanup or self-audit item) and whose `.audit.md` exists and is not blank, in any status (a red
item's notes are still notes). It is *used* once an `audit_used` event names it, and
*claimed* while a running cleanup's `.run.json` lists it under `notes`. A cleanup or
general cleanup takes every note neither used nor claimed at the moment it launches (they
are in its `start` event as `notes`). When it concludes, unless it ended red, conductor logs
`audit_used` (`notes`, and their files as `paths`) before its `finish`, so the next
cleanup sees only newer notes. A red one uses none: they stay for the next one. A
general cleanup that does not end red uses its own notes the same way. Notes another item
already handled are marked by hand with the `audit_used:` signal (`signals.md`). A cleanup that
could run but has no unused notes holds (`hold`: *cleanup: no unused audit notes*)
until an audit item finishes or it is cancelled. While something else still blocks
it, it only waits, since the item it waits on may add notes.

`conductor audits [<control>]` lists each repo's unused notes (item, status, finish
time, file, line count); `--all` adds the used ones with the cleanup that used them, and those a running cleanup has claimed.
Whenever a repo has notes no cleanup has used or claimed, it shows an **audit pending**
marker, so Matt can see a cleanup is due whenever it next makes sense. The inbox's *Now*
line for the repo and `conductor status` (under the repo) say
`audit pending: N audit notes (NNN_x, …)`. The app's Repos tab ends the repo's header
with `· audit pending: N audit notes`, drawn amber. The marker clears as soon as a
cleanup claims the notes (from when it launches) or uses them. All three share
`audits.pending`.
