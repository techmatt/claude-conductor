# Queue items

## Queue items

`queue/<repo>/NNN_name.md`: YAML front-matter, then the prompt body. A name shaped like
an amendment (`NNN_name.amend[.<tag>].md`) is never an item: it is invalid if parsed as one.

```
---
repo: sandbox            # required; must match the queue folder
locks: [shared]          # named locks beyond the repo's own implicit lock
depends_on: [001_hello]  # 001_hello, 001, other-repo/001_x; each must have finished `done`
after_red: stop          # stop (default) | continue
needs_planner: false     # true: the repo's next item waits until the read mark passes this finish
readonly: false          # true: conductor checks the repo is unchanged afterwards
max_hours: 1             # hard-kill deadline; default from conductor.toml
alongside: the recorder's save path only   # one line: may start beside running items (below)
paths: ['src/ui/**']     # legacy: globs the item may change (see `paths:`)
stage: false             # override the repo's `stage` setting for this item
ultracode: true          # launch with Claude Code's ultracode on (see executors.md, *Ultracode*)
audit: true              # keep audit notes beside the report (see templates.md)
template: cleanup        # a conductor-owned prompt (templates.md): cleanup, general_cleanup, test_cleanup, …
resume: sandbox/001_x    # ask a finished item's own session, forked; always readonly (resume.md)
---
Prompt body.
```

Unknown fields, a mismatched `repo`, bad values or an empty body (allowed with
`template:`, whose text is the prompt) make the item **invalid**: logged once (and again whenever the file changes) and its queue holds
until the file is fixed — an item not yet started may be rewritten freely.

**Rewriting a queued item.** Conductor parses `queue/` afresh every tick, so a rewrite
of an item that has not launched takes effect: the launch reads the file as it is then.
Each change to a queued file's bytes logs an `edited` event (an Activity event, so the
planner and the Events tab see that the rewrite landed). A stage belongs to the text it
read, so a rewrite of a staging or staged item discards the stage (a staging executor is
killed) and stages it again from the new text when it still waits (`warning` *stage
discarded … it starts again from the new text*; `worktrees.md`, *Staging*). A rewrite
that lands mid-launch, between the tick's scan and the move into `running/`, undoes that
launch (its worktree or stage discarded, `edited` with `launching: true`) and the next
tick launches the new text. Once the item has moved to `running/`, a write to its queue
file can't reach it: it is set aside as `NNN_name.md.bad` with an `invalid` event, never
left to hold the queue (the same for a queue file named like an item in `done/`). A stage
found changed after a restart logs `edited` too. To add to a plan that still stands,
keeping a stage, or to change a running item, amend it instead (`progress.md`, *Amendments*).
Its receipt: `receipts/queue/<repo>/NNN_name.md` (`receipts.md`).

An unquoted `depends_on: 001` is the YAML integer 1; conductor matches bare numbers
numerically, so it still resolves. A number two items share (`receipts.md`)
names neither: a `depends_on` on it holds.

A `depends_on` that can never be met in order is flagged when the item is queued or
rewritten, not only as a hold once it is the head: the receipt stays `accepted` and gains
a `warning` naming each such dependency and why (later in the same queue; already ended
`partial`, `red` or the like; cancelled), and the `enqueue` event carries it too
(`state.dependency_warning`). A dependency not written yet is not flagged.

**Scheduling** (order, holds, readonly, `alongside` and exclusive items): `scheduling.md`.

Items move `queue → running → done` by atomic rename.

**max_hours** is enforced by the daemon: past the deadline (stored in `.run.json`, so
it survives restarts) the executor's whole process tree is killed (`kill` event). Time
blocked on a question does not count: no kill while a blocking ask is open, and when it
resolves the deadline moves later by the time it was open (`deadline` in the `answer`
event; `blocked_seconds` in `.run.json`). Time waiting on a defaulted ask does count.

**readonly** items: conductor records the HEAD and tracked-file status of the item's
worktree (or repo) before and compares after; any difference is red. Commits made by
any item are logged as `commit` events (`scope` = the repo, `branch` when in a
worktree) when it exits; the merge then lands them on `main` rebased, under new shas.

## `paths:` concurrency

Legacy: still honoured, but no longer offered to planners; `alongside` replaces it. An item may declare `paths:` — globs relative to the repo root, `/`-separated. `*`, `?`
and `[...]` match within a segment, `**` any number of segments, and a glob also covers
everything under a folder it matches (`docs` = `docs/**`); case is ignored.

- **Running together.** A queue head may start while other items of its repo run (or
  wait to merge) only if the repo uses worktrees, both items declare `paths`, every
  pair of their globs is disjoint, and they share no named lock. Otherwise the repo
  lock is exclusive, except that a readonly item never takes it (`scheduling.md`, *Readonly items*). Disjointness is conservative: two globs count as disjoint only
  when, segment by segment, two literal segments differ or a literal fails a wildcard;
  two wildcard segments, a `**`, or one glob being a prefix of the other count as
  overlapping.
- **Enforcement.** After the run, every file the branch changed (`git diff` from the
  cut point) must match a declared glob. Anything else is a `paths_violation` event
  and the item ends red, unmerged. Enforced in `worktrees = false` repos too.
- **Prose files.** Each repo's `prose` globs (default `["CLAUDE.md", "README.md",
  "docs/**/*.md"]`; `prose = []` turns it off) name the shared prose nearly every item
  touches. Changes to them never count against `paths`: an item may edit them without
  declaring them, and two items that both edit `README.md` still run together when
  their code paths are disjoint. A declared glob that names only prose (a prose glob
  itself, or a literal path one covers, like `README.md` or `docs/a/b.md`) is left out
  of the disjointness check, so an item whose `paths` are all prose shares with
  anything that declared `paths`. A wildcard like `*.md` or a folder like `docs` is
  not prose-only and still counts. At merge, prose merges textually through the
  rebase; a real conflict there goes to a merge executor (`worktrees.md`,
  *Prose conflict*). The preamble tells a `paths` item its prose files are exempt.
