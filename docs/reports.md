# Reports

How an executor's report is read: the header it starts with and the outcome that follows.

## Report header

The report starts with:

```
status: done | partial | red
blocks: <what this blocks, or none>
carry: <what state.md should change, or none>
slow: none | <component> ~<time>, <why>, <what would fix it>[; <another>]
friction: none | <what cost the item time or tokens>[; <another>]
pick: <manifest path relative to the repo root>     # optional: only after writing a picker manifest
```

(Bare lines or inside a `---` block; `**status**:` is tolerated.) Only the first six
non-blank lines are read: `status`, `blocks` and `carry` count only in the first three
(the first of each); `slow:`, `friction:` and `pick:` only straight after them, in any
order, each counted only when every line before it was a header line; `pick:` with a one-word value; so a body that opens with
"Status: …" or "Pick: went with …" stays body. Backticks around a pick are dropped, `\`
becomes `/`, and `pick: none` is no pick. The `finish` event carries it as
`pick: <repo>/<path>`, the ref the `pick:` signal takes.

`slow:` is where the executor's time went, the one thing an audit can't reconstruct
later: a component listed only if it took over ~15 minutes or about twice what was
expected, with a coarse time, the cause and a fix when one is visible (a test-lane
split, a fact missing from README or CLAUDE.md, a slow build step, a tool that kept
failing, too much searching). `none` is the normal answer. The preamble asks for it from
every item, template items included. A report without it still parses (`slow` is null:
no red, no warning). The `finish` event carries it as written; the inbox's *Unreviewed
reports* line, `conductor events`, the app's Activity line and the Repos tab's note
(whole in its tooltip) show it unless it is `none` or absent. The planner reads these
lines as a maintenance trigger (`planner.md`, *Maintenance*).

`friction:` (59) is a free signal on every report: one or two things in the prompt, the
repo's docs, its tools or conductor that cost the item time or tokens (`none` is fine).
It is carried, shown and counted like `slow:`, and it feeds the digest's workflow-retro
line (`inbox.md`, *Maintenance facts*). Parked edits an item carries are answered just
after the header as `parked P<n>: done | skipped (why)` (`parked.md`).

## Outcome

The item's final status is the report's status unless something makes it
red: no report or a bad header, no `result` line in the log, `is_error` in the result,
killed at `max_hours`, interrupted (restart rule), control-folder tamper, a readonly
item that changed its repo, a launch failure (worktree or setup included), a
`paths` violation, uncommitted changes left in the worktree, or a rebase conflict
(including a prose conflict its merge executor failed to resolve).
The reasons are listed in the `finish` and `red` events.
