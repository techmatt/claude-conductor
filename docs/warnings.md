# Warnings

A report's `warnings:` line carries automatic warnings of any kind to the planner and
Matt without anyone hunting for them (109). First user: aicomplete's design checker,
whose tripped checks (a capstone resource with a cheap source, the same power on every
board, games too short) had been built and calibrated around unnoticed.

Warnings are surfaced, never gated: one never changes an item's status, stops it or holds
its queue. Projects define their checks; conductor carries and shows what trips.

## The line

```
warnings: none | <kind>: <what tripped>[; <kind>: <another>]
```

A header line beside `slow:` and `friction:` (`reports.md`), read the same way: only
straight after the first three lines, absent is no problem, `none` is none. The kind is
one word (`design`, `data`, `perf`, `docs`, `security`, or anything a project defines);
a part without `<kind>:` counts as `other` (`inbox.warning_parts`). Every preamble (full,
light and watch) asks for it: the executor runs its repo's checks and reports what
trips, and may raise any other warning it notices.

## Declaring a project's checks

Two places, either or both, no conductor change needed:

- **`CLAUDE.md`, a *Warning checks* section**: the checks in prose (what to run, what
  trips, which kind). The preamble points every executor at it by that name, so it is
  the natural home for a check that needs judgement or only applies to some items.
- **`warning_checks` in `[repos.<name>]`** (`conductor.toml`, `control-folder.md`): a list
  of commands, named in the preamble as "and `<command>`; …" so every executor runs them
  before its report. Use it for a script that prints what trips.

A command should exit 0 and print one line per tripped check, `<kind>: <text>`, which
the executor copies into its line. Example, aicomplete:

```toml
[repos.game]
path = "../aicomplete"
warning_checks = ["uv run python tools/design_check.py --brief"]
```

A new project is offered checks for its domain at setup (`claude-ai-new-project.md`).

## Where warnings show

- The `finish` event carries `warnings` as written (`events.md`); `conductor events`
  and the inbox's event lines add ` · **warnings: …**`.
- **`## Warnings`** in `inbox.md` and `session.md` (after *Questions* and *Staged plans'
  questions*, before *Unreviewed reports*) and in `review.md` (before *Unreviewed
  reports*): one line per unreviewed finish that raised any, `- <item> (<status>,
  finished <clock>, #<id>): **<kind>**: <text>; …`, then for each repo a line counting
  the kinds shown that repeat in its last five reports: `- repeated in `<repo>`'s last 5
  report(s): design ×3 (205, 206, 207)`. A line leaves once its report is reviewed.
- **Maintenance facts** (`session.md`): per repo, ``- `warnings:` kinds repeated in the
  last N report(s): <kind> (<numbers>); …`` or `none`, like `slow:` and `friction:`.
- **A toast** for each finish whose report raised any: "<project>: <item> raised
  warnings", the line as its body (`inbox.md`, *Toasts*).

The planner raises a `warnings:` line to Matt first, with the item's own judgement
(`planner.md`, *Reading a report*).
