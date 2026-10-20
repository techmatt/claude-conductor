# Queue items

## Queue items

`queue/<repo>/NNN_name.md`: YAML front-matter, then the prompt body.

```
---
repo: sandbox            # required; must match the queue folder
locks: [shared]          # named locks beyond the repo's own implicit lock
depends_on: [001_hello]  # 001_hello, 001, other-repo/001_x; each must have finished `done`
after_red: stop          # stop (default) | continue
needs_planner: false     # true: the repo's next item waits until the read mark passes this finish
readonly: false          # true: conductor checks the repo is unchanged afterwards
max_hours: 1             # hard-kill deadline; default from conductor.toml
paths: ['src/ui/**']     # globs the item may change; enables concurrency (see `paths:`)
stage: false             # override the repo's `stage` setting for this item
ultracode: true          # launch with Claude Code's ultracode on (see executors.md, *Ultracode*)
audit: true              # keep audit notes beside the report (see templates.md)
template: cleanup        # cleanup | general_audit | test_perf | disk_audit | claude_md_cleanup | bug_hunt: a conductor-owned prompt (templates.md)
---
Prompt body.
```

Unknown fields, a mismatched `repo`, bad values or an empty body (allowed with
`template:`, whose text is the prompt) make the item **invalid**: logged once (and again whenever the file changes) and its queue holds
until the file is fixed — an item not yet started may be rewritten freely.

**Rewriting a queued item.** Conductor parses `queue/` afresh every tick, so a rewrite
of an item that has not launched takes effect: the launch reads the file as it is then.
Each change to a queued file's bytes logs an `edited` event (an Activity event, so the
planner and the Events tab see that the rewrite landed). A staged item's stage belongs
to the text it read, so a rewrite discards it and the item launches fresh (see
`worktrees.md`, *Staging*). Once the item has moved to `running/`, edits are never read. Changes made
while the daemon is down log no `edited` (the first scan sees only the new text), but a
stage is still discarded by its hash. To add to a plan that still stands, keeping a
stage, or to change a running item, amend it instead (`progress.md`, *Amendments*).

An unquoted `depends_on: 001` is the YAML integer 1; conductor matches bare numbers
numerically, so it still resolves.

**Scheduling.** Each repo starts its items strictly in NNN order; the head of a queue
that cannot run holds everything behind it. Different repos run in parallel. The head
runs when all of these hold:

- no item of that repo is running or waiting to merge (the implicit repo lock), unless
  every such item may share the repo with it (see `paths:`), which a readonly item
  always may (*Readonly items* below);
- the repo is not held after a red (below) and no `needs_planner` report is unread;
- every named lock is declared in `conductor.toml` and free;
- every `depends_on` item finished with status `done` (a dependency still queued
  or running elsewhere means *wait*; one that ended `partial`/`red`, is unknown,
  or sits later in the same queue means *hold*);
- the item id has not run before (a re-run is a new item number).

**Holds** are logged as `hold` events (once per reason change). Waiting on a busy lock
or a running dependency is not a hold. A finished item waiting to merge keeps the repo
lock but releases its named locks.

- *after a red*: when an item ends red and its `after_red` is `stop`, its repo's queue
  holds until a `clear: <repo>` signal. `continue` items don't hold.
- *needs_planner*: the repo's next item holds until the read mark is at or past the
  finished item's `finish` event id.

**Readonly items** take no repo lock (in a repo with worktrees; with `worktrees = false`
they share the one checkout, so the lock stays). They never merge, so nothing waits for
them and they wait for nothing in the repo: a readonly head starts at once beside
whatever runs or waits to merge, and the items after it don't wait for it. Everything
else still applies: its named locks, `depends_on`, the holds, its own worktree
(discarded at the end), and the readonly and tamper checks. Start order is unchanged.
A readonly item still starts only once it is the head, so one queued behind a waiting
item waits for that item to *start*, not to finish. It sees `main` as it is when it
launches, so one meant to look at an earlier item's work declares `depends_on` on it.
`conductor status` marks it `readonly`, and the app's *In flight* row reads
`running · readonly`.

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

An item may declare `paths:` — globs relative to the repo root, `/`-separated. `*`, `?`
and `[...]` match within a segment, `**` any number of segments, and a glob also covers
everything under a folder it matches (`docs` = `docs/**`); case is ignored.

- **Running together.** A queue head may start while other items of its repo run (or
  wait to merge) only if the repo uses worktrees, both items declare `paths`, every
  pair of their globs is disjoint, and they share no named lock. Otherwise the repo
  lock is exclusive, except that a readonly item never takes it (*Readonly items*). Disjointness is conservative: two globs count as disjoint only
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
