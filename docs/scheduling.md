# Scheduling

When a queued item starts: the order within a repo, holds, and the items that start
early or run alone. The item file itself: `items.md`.

Each repo starts its items strictly in NNN order (readonly and
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
repo with worktrees; no red (a `resume:` item excepted, `resume.md`) or unread `needs_planner` holds the repo; it is valid, not
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

**Exclusive templates** (`cleanup`, `general_cleanup`, `test_cleanup`, `disk_cleanup`,
`claude_md_cleanup`, `content_cleanup`; not `bug_hunt`, `self_audit` or `watch`, which starts beside anything) run alone in their repo: one at the head waits for
every running item, readonly and `alongside` ones included; nothing, readonly items
included, starts beside one running; and nothing behind one in the queue starts early,
so a stream of small items never starves it. Readonly yields here because a perf or
disk measurement is skewed by anything else running, and a repo-wide pass should see
the repo still.
