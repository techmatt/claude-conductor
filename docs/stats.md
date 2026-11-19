# Stats

`conductor stats [project] [--weeks N]` (`src/conductor/stats.py`, 139) prints a scoreboard
read from one project's `events.db`, so changes to how items run (a preamble rewrite, the
diff review, the Opus / Fable toggle, a template's rules) are judged by numbers, not feel.
A planner gets the same text from `read(what="stats", project, weeks?)` (`mcp.md`; `name`
may carry the weeks too). It only reads: no events, no files, nothing a daemon does changes.

`project` is a name the app remembers (or a `*-control` folder beside this checkout holds),
or a control folder; with none, `$CONDUCTOR_CONTROL`, then the current folder. `--weeks`
(default 4) counts this week; weeks start Monday, local time. The text stays near 2 KB.

## What it shows

- **Items without a template, per week and model**: finished (each item's last `finish`
  in the week), `done/partial/red`, the share whose report had a real `friction:` or `slow:`
  line (not missing, empty or `none`), median hours (first `start` or `stage` to `finish`)
  and median cost (`finish` `cost_usd` plus any `staged` cost), questions asked (`ask`) and
  how many timed out to their default (`default`). The model is the `finish` event's (else
  the `start`'s), short (`fable-5-2`); runs before 135 show `unrecorded`.
- **Spend per week, by kind**: every finished item, `feature` (no template) or its
  template (`bug_hunt`, `self_audit` for retros, `watch`, the cleanups), largest first, with
  its share of the week's dollars and its executor hours; past five kinds the rest are
  summed as `other`.
- **Corrections**: items queued in the week to fix a finished one, of the non-template
  items queued, with the reason and the item numbers.
- **Planner writes per session**: a session runs from its `session:` checkpoint to the
  next. Each week's mean, then the latest six sessions: requests (and how many were
  refused), planner signal files, items queued and rewritten (`enqueue` / `edited`, not
  conductor's own watches, retros and backlog draws), amendments and answers `by: planner`,
  and `mcp`: the writes known to come through the MCP tools.

## The corrections rule

Cheap and stable, from what the log carries. An item counts when it is queued with no
template and:

- `resume_of` is set (`resume`), or
- a `depends_on` names an item already finished when it was queued (`depends`), or
- its slug holds a finished item's number in its repo (`fix_278`) or the word `followup` /
  `followups` (`name`);

and every `land:` begun (`land`: a kept red branch put on main) counts as one.

It misses a fix asked for in an unrelated item's prose, a follow-up whose name doesn't
point back, an amendment to a running item, and a planner's own edit. It overcounts a
`followups` item that is new work, and a `depends_on` used only for ordering. Retros
(`self_audit`) are not corrections; their spend shows under *by kind*.

## The planner counts

What the log can't see: reads (`status`, `read`) leave no trace, so `mcp` counts only
MCP writes it can tell apart: signal files named `planner-<stamp>-<nnnnnn>.signal` (the
bridge's have no counter) and requests named `…-mcp…`. MCP queue items, amendments,
answers and parked lines look the same as the bridge's and are counted in their own
columns, not in `mcp`.
