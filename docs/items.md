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

**Scheduling.** Each repo starts its items strictly in NNN order (readonly and
`alongside` items excepted, below); the head of a queue that cannot run holds everything behind it. Different repos run in parallel. The head
runs when all of these hold:

- no item of that repo is in setup, running or waiting to merge (the implicit repo
  lock; the wait reads `repo busy: <item> setting up` during setup), unless
  every such item may share the repo with it (`state.can_share`): a readonly or
  `alongside` item always may, either side (*Readonly items*, *Alongside items* below),
  and so may two with disjoint legacy `paths`; an exclusive template never shares;
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
them and they wait for nothing in the repo: a readonly item starts as soon as it is
queued, **wherever it sits in the queue**, beside whatever runs, waits to merge, waits
or is staged; the items around it keep their order, numbers and stages (a staged head
stays staged and resumes later). Behind the head it starts only when: the repo is a git
repo with worktrees; no red or unread `needs_planner` holds the repo; it is valid, not
run before and not staged; its named locks are declared and free (not held by a running
item, a head starting this tick or another readonly item started this tick); every
`depends_on` finished `done` (one queued or running makes it wait); it is not
mid-amendment or in a repo being reordered; memory is `ok`; no exclusive template runs
in the repo or sits ahead of it in the queue. An exclusive template (below) never
starts early. One that would be held logs no `hold` until
it reaches the head (`state.early_starts`). Everything else still applies: its own
worktree (discarded at the end), and the readonly and tamper checks. It sees `main` as
it is when it launches, so one meant to look at an earlier item's work declares
`depends_on` on it. `conductor status` marks it `readonly` (a non-head one shows under
its repo while lower items still show queued or staged), and the app's *In flight* row
reads `running · readonly`.

**Alongside items.** `alongside: <one line on what this item touches>` is the planner's
judgement that the item is unrelated to whatever runs in its repo. It takes no repo lock,
either way round: it starts beside items running, in setup, waiting to merge or staged,
and any item may start beside a running `alongside` one. Behind the head it starts early
under the readonly rules above (named locks, `depends_on` done, holds, amendments,
memory). It still runs in its own worktree and merges when it finishes, one merge at a
time; a conflict is handled as any conflict. Only with worktrees: with `worktrees = false`
it waits its turn like any item. It must be one non-empty line, and a `template:` item
with `alongside` is invalid (held: templates are repo-wide and wait their turn).
Whenever an item starts its executor beside others and either side is `alongside`,
conductor warns both: the new item's prompt gains a *Beside you* paragraph listing each
such item running, in setup or staged (`<id>: <gist>`, the gist its `alongside` line,
else its body's first heading, else `the <template> template`, else its name;
`Item.gist`), asking it to stay out of their areas and
say in its report if it had to touch them; and each one executing gets a message on the
`tell:` path (a `tell` event, `by: conductor`, `beside: <new id>`): "`<id>` started beside
you: `<gist>`. Likely unrelated; avoid those files, and say in your report if you had to
touch them." A pair sharing only by readonly or `paths` hears nothing, and an item
whose paragraph already named the new one (it was in setup or staged then: the run
record's `warned`) gets no tell about it. The `start` event carries `alongside` and
`beside` (the ids executing or in setup in the repo then). The run record's `beside`
also gains each item that starts later beside it, so the inbox's *Now* line reads
`NNN_name (alongside, beside <ids>)` (either part alone when only it applies),
`conductor status` adds `, alongside` and `, beside <ids>`, and the app's *In flight* row
reads `running · alongside` with `beside …` and the line in its note.

**Exclusive templates** (`cleanup`, `general_audit`, `test_perf`, `disk_audit`,
`claude_md_cleanup`; not `bug_hunt`) run alone in their repo: one at the head waits for
every running item, readonly and `alongside` ones included; nothing, readonly items
included, starts beside one running; and nothing behind one in the queue starts early,
so a stream of small items never starves it. Readonly yields here because a perf or
disk measurement is skewed by anything else running, and a repo-wide pass should see
the repo still.

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
