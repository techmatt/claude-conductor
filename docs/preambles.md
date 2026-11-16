# Preambles

What conductor tells an executor before the item body (`executor.py`); how it is launched,
its MCP and settings files, `wait_for` and message delivery: `executors.md`. **Every rule a
preamble carries, its origin and where it lives now (text, hook or dropped) is in the rule
ledger, `hooks.md`.** This file is the structure.

Since 123 the preambles are short general guidance, not one rule per incident (Anthropic's
Claude Code team found frontier models do better that way, and so did Matt's executors).
A rule the hook enforces keeps at most a clause; incident citations stay out of the
executor's text and live in the ledger. A rule dropped there comes back when a retro or a
`friction:` line shows its failure again, preferably as a hook.

## Three preambles

Chosen by conductor, never by front-matter:

- **Watch** (`WATCH`, ~150 words) for `W<n>_watch` items: one `progress` line, scratch,
  messages, the readonly line, the time limit and the report rules. Out of 123's scope.
- **Light** (`LIGHT`, ~295 words of template, ~380 rendered) for a small item
  (`executor.is_light`): a body under 150 words (`LIGHT_WORDS`), none of `ultracode`,
  `template`, `resume`, `audit` or an explicit `max_hours`, and nothing that could produce
  candidates (`PICK_RE`: a short art round needs the picker schema). *Asking* (with the
  two design asks a small item meets), *Progress* (start and long jobs), *Bounds* (skips,
  scratch, compute, a clause on the hooks, the leftover line), *Latitude* (the conflict
  order, bars, the fix-loop cap, fix the obvious), messages, `CALLS_LIGHT`, where it works,
  a port line where the repo serves, the time limit and the report header. No outline,
  ETAs, pickers, servers or diff review.
- **Full** (`PREAMBLE`, ~885 words before its optional blocks) for everything else:
  *Asking* (the design-ask list word for word), *Latitude* (the conflict order, method,
  bars, the fix-loop cap, the wrong-repo guard as the only hard stop, fix the obvious, the
  leftover check), *Measurement* (paired and fresh seeds, a quiet box, ratios beside
  others, re-read quoted numbers), *Progress* (with `outline` and `eta`), *The box*
  (scratch, results, keeps, a clause on the hooks, load, helpers), *Messages*, then the
  optional blocks, where it works, the time limit, the **report-path line** (word for
  word: `tests/fake_claude.py` and the restart preamble find the path by it), the header,
  the status definitions and the report rules.

## Optional blocks (full preamble)

- *Calls* (`CALLS`; light `CALLS_LIGHT`): the `foreground_seconds` cap and `wait_for`,
  unless the cap is 0 (`progress.md`, *Short calls*).
- *Servers* (`SERVERS`) where the repo can serve (`executor.has_servers`: a run target's
  `cwd` in the repo, or a `package.json` at its root or one folder down with a `dev…` or
  `preview…` script): the port, `start`, and `npm ci` before a package's checks. Light
  gets `PORT_LIGHT` instead.
- *Calibrate* (`CALIBRATE`) for `rules: true` or a body naming a gate, band, target or
  seed count (`executor.wants_calibrate`): the freeze line, the SE gap, the diff review
  before fresh seeds, seeds taken in the ledger.
- *Pickers* (`PICK`) and the header's `pick:` line when the body could produce
  candidates (`executor.PICK_RE`; schema: `picks.md`).
- *Audit notes* (`templates/audit.md`) for `audit` items and note-keeping templates, with
  `AUDIT_FIX` for a writable `audit: true` item.
- **Diff review** (`REVIEW`, 123) for a writable full item that isn't a template: before
  the report, one read-only subagent gets `git diff <base>` (the worktree's base, or the
  main checkout's head at launch: `run["repo_head"]`), the prompt's rulings word for word,
  the done-when list and the repo `CLAUDE.md`'s *What failure looks like* section if any.
  It reports bugs and unmet or broken done-when rows, each with a file and line, no style
  comments; the item fixes what is real, at most one more pass on the new diff, and the
  report body carries `Diff review: N found, M fixed, K rejected (why)`. An `ultracode`
  item gets `REVIEW_ULTRA`: its adversarial review counts as this one. Light, template and
  readonly items skip it.
- Stage and resume texts (`STAGE`, `RESUME_HEAD`, `STAGE_DEFAULTS`, the amendment
  blocks): `worktrees.md`, *Staging*.

Template texts follow the preamble unchanged; a template item but a watch always gets the
full one, as do `audit` items and resume items (with *Resumed*). A **readonly** item in a
worktree, light or full, gets the readonly line in place of the worktree's commit and merge
paragraph and no leftover step; not a watch, the line adds that nothing merges, so ledger
entries and anything the prompt says to commit go in `carry:`.

## Filled in at launch

- **Running beside you now** ends the working-directory lines of a full or light preamble:
  the project's other items executing or in setup, any repo, not paused, no watch, or
  "nothing" (`talk.running_beside`, kept in the run record as `running_beside`). An
  `alongside` item can start later, so on a change the next `progress` or `wait_for`
  return ends with the new line, once. Other projects' daemons don't show here; *Now*'s
  load line does.
- **Port.** Every launch (a resumed stage too) gets a TCP port of its own: free at launch,
  from the OS's ephemeral range, never a run target's or tool's, another live item's or the
  picker's. It is in the run record and the `start` event (`port`), and in the executor's
  environment as `CONDUCTOR_PORT`; the preamble names it only where the repo can serve.
  Stages and merge executors get none.
- **Threads.** `{free}` is `load.FREE_THREADS`, `{threads}` `load.run_threads()` (the box's
  less 4 and 2 of headroom). `load_high` logs under 2 free for ~3 min and messages only an
  item that is a real share of it; a Git Bash `nice -n 10` gives `BELOW_NORMAL` on Windows,
  inherited by children.
- **Scratch.** The daemon makes `out/<item>/` at launch; `out/keep/` stays shared and
  outlives the worktree (`worktrees.md`, *Kept reads*). `out/` is ignored before every
  launch and stage (`git.ignore_locally`: `/out/` in `.git/info/exclude`, never committed).
- **Warnings.** The header's `warnings:` line names the repo's CLAUDE.md *Warning checks*
  and its `warning_checks` (`executor.checks_text`; `warnings.md`).
- **Writable roots.** `write_roots` (`control-folder.md`; default `open_roots`) are named
  writable (`WRITES`) and the only-file line allows them; not readonly items or watches.
