# Audits and templates

Code-quality audits are conductor-owned texts, not prompts the planner retypes. They
live in `src/conductor/templates/` (`audit.md`, `cleanup.md`, `general_audit.md`,
`test_perf.md`, `disk_audit.md`, `claude_md_cleanup.md`, `bug_hunt.md`) and are versioned with this repo: the `start` event of a template item
carries `template` and `template_version` (the text's sha256, first 12 hex digits), so
`git log` on the file finds the exact text sent. Executors never see the control
folder, so whatever a template needs is inlined into the prompt (kept, as always, in
`.prompt.md`). The texts use `{{name}}` placeholders.

- **`audit: true`** (any item; explicit, never automatic). The preamble gains an
  *Audit notes* paragraph (`audit.md`) naming an absolute path,
  `reports/<repo>__NNN_name.audit.md`, as the second file the executor may write
  outside its working directory. It keeps a running notes file there while it works,
  covering every task, under four headings: what was poorly structured, what took
  longer than expected and why, what should be refactored or cleaned up, and what
  CLAUDE.md or README should say. Nobody reviews notes per item. The item's `finish`
  event carries `audit: true` and lists the notes file among its paths.
- **`template: cleanup`**. The prompt after the preamble is `cleanup.md` with every
  *unused* note of the repo inlined, oldest first, each headed `### Notes from <item>,
  finished <status> <time>`; then, after `---` and "The planner adds, for this item:",
  the item's own body, if any. The executor carries out what is clear and well
  defined and defers every design-level call (its report lists them under
  **Deferred**, with options and a recommendation). A cleanup never stages.
- **`template: general_audit`**. The same, with no notes to go on: it behaves as
  `audit: true` (it writes its own notes file), audits the whole repo, then carries out
  the clear items and defers the design calls.
- **`template: test_perf`**. Set up or tighten a fast/slow lane split and speed tests
  up generally; a small loss of thoroughness or moving a slow test to the slow lane is
  allowed; the report gives before/after timings per lane (the one report where test
  detail belongs). It uses no notes.
- **`template: disk_audit`**. Find why the project takes so much disk and free what
  can safely go, under the project's storage policy (`claude-ai-new-project.md`, *Storage*):
  delete what is cheaply rebuilt or clearly obsolete, move what is expensive or
  irreplaceable but not needed day to day into `archive\` under the project's Drive
  home, never delete anything hand-made; history rewrites and other destructive calls
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
  disk audit never stages.
- **`template: claude_md_cleanup`**. A quick pass through the repo's `CLAUDE.md`:
  cut what is extraneous or duplicated (a README fact gets a pointer, not a copy), fix
  what is wrong against the code, tighten the wording, keep the standing contract the
  new-project doc requires. Changes to how executors work go under **Deferred**. The
  report is short: **Cut**, **Fixed**, **Deferred**.
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
  re-reported. `max_hours` is the hunt's target plus an hour for setup and the report
  (the vocab queues N + 1), and the target is not a cap. The planner's body, if any,
  usually names a focus. With `readonly: true` it is *notes only*: the text's own
  paragraph has it find and record but change nothing tracked (checker and
  screenshots in a `shared` folder or nowhere, findings in the report only). The
  report has **Checker**, **Fixed** (notes only: **Found**) and **Deferred**, most
  important first, each with options and the executor's pick.

**Which notes are unused.** A note is a finished item whose `finish` event has
`audit: true` and whose `.audit.md` exists and is not blank, in any status (a red
item's notes are still notes). It is *used* once an `audit_used` event names it, and
*claimed* while a running cleanup's `.run.json` lists it under `notes`. A cleanup takes
every note neither used nor claimed at the moment it launches (they are in its `start`
event as `notes`). When it concludes, unless it ended red, conductor logs
`audit_used` (`notes`, and their files as `paths`) before its `finish`, so the next
cleanup sees only newer notes. A red cleanup uses none: they stay for the next one. A
general audit that does not end red uses its own notes the same way. A cleanup that
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
