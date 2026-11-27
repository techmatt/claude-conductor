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
  scratch, compute, the leftover line), *Latitude* (the conflict
  order, bars, the fix-loop cap, fix the obvious), messages, `CALLS_LIGHT`, where it works,
  a port line where the repo serves and the report header. No outline,
  ETAs, pickers, servers or diff review. Its first line carries the time limit and kill time
  (147), its second the file-write rule (Write/Edit only, the forms the hooks refuse).
- **Full** (`PREAMBLE`, ~900 words before its optional blocks) for everything else: the
  time limit and kill time in its first paragraph (147: aicomplete 316 met its 4h cap only
  on resume, after planning an overnight farm), then
  *Files* (146: Write/Edit or one checked script file run alone, in `out/<item>/`, written
  by Write, never a heredoc (148: a heredoc loses a level of backslash escaping under
  conductor, helium-hustle 067, 070, 071; the hook still lets one into `out/` through,
  147); the hooks refuse `sed -i`, heredoc-fed Python (`python - <<EOF`, named since 151:
  helium-hustle 102, 103, 105 still lost calls to it), a writing `-c`/`-e` and a heredoc into
  a file, and Claude Code's bypass-mode note suggesting shell edits does not apply (151: executors run under
  `--dangerously-skip-permissions`, whose system prompt prefers `sed` and heredocs; light says
  both too)), *Asking* (the design-ask list word for word), *Latitude* (the conflict order, method,
  bars, the fix-loop cap, the wrong-repo guard as the only hard stop, fix the obvious, the
  leftover check, which since 151 asks `design: true` before finishing partial when the only
  unmet bar needs reads outside the prompt's allowed scope, helium-hustle 112), *Measurement* (paired and fresh seeds, a quiet box, ratios beside
  others, re-read quoted numbers), *Progress* (with `outline` and `eta`), *The box*
  (scratch, results, keeps, the other refused forms, load, helpers; the results folder is the
  repo's `results`, default `experiments/results`, "unless the repo's CLAUDE.md files them
  elsewhere"; since 151 a `results` under `out/` or git-ignored, as fractals' repos ignore
  `experiments/`, gets `PAPERS_KEPT`: papers in `out/keep/<item>/`, named at their kept path,
  nothing committed, `kept.no_results`), *Messages*, then the
  optional blocks, where it works, the **report-path line** (word for
  word: `tests/fake_claude.py` and the restart preamble find the path by it), the header,
  the status definitions and the report rules.

## Optional blocks (full preamble)

- *Calls* (`CALLS`; light `CALLS_LIGHT`): the `foreground_seconds` cap and `wait_for`
  (128: for a background subagent, `minutes` near its length, never a path nothing will
  write), unless the cap is 0 (`progress.md`, *Short calls*).
- *Servers* (`SERVERS`) where the repo can serve (`executor.has_servers`: a run target's
  `cwd` in the repo, or a `package.json` at its root or one folder down with a `dev…` or
  `preview…` script): the port, `start`, and `npm ci` before a package's checks. Light
  gets `PORT_LIGHT` instead.
- *Calibrate* (`CALIBRATE`) for `rules: true` or a body naming a gate, band, target or
  seed count (`executor.wants_calibrate`): the freeze line, the SE gap, seeds taken in the
  ledger. (Its "diff review before fresh seeds" moved into *Diff review* in 144.)
- *Pickers* (`PICK`) and the header's `pick:` line when the body could produce
  candidates (`executor.PICK_RE`; schema: `picks.md`).
- *Audit notes* (`templates/audit.md`) for `audit` items, note-keeping templates and (146)
  every readonly item but a watch, in the light preamble too, with `AUDIT_FIX` for a writable
  `audit: true` item (`audits.writes_notes`).
- **Diff review** (`REVIEW`, 123) for a writable full item that isn't a template: before
  the report, one read-only subagent gets `git diff <base>` (the worktree's base, or the
  main checkout's head at launch: `run["repo_head"]`), the prompt's rulings word for word,
  the done-when list and the repo `CLAUDE.md`'s *What failure looks like* section if any.
  It reports bugs and unmet or broken done-when rows, each with a file and line, no style
  comments; the item fixes what is real, at most one more pass on the new diff, and the
  report body carries `Diff review: N found, M fixed, K rejected (why)`. An `ultracode`
  item gets `REVIEW_ULTRA`: its adversarial review counts as this one. Light, template and
  readonly items skip it. Both say a rules change gets it before fresh seeds, and that a
  self-check of the config diff is not it (144, aicomplete 300).
- **Standing rulings** (137): when the item's worktree (else the repo's main checkout) has
  `docs/rulings.md` (`executor.has_rulings`), *Latitude* names it first among the locked
  rulings ("read before you start"; the light preamble: "read it first"), the diff review's
  brief carries it whole (`REVIEW_ULTRA`: "checked against" it). No file: nothing is said.
  A watch never gets it.
- **Bug hunts** (137) get no *Leftover budget* line, in the preamble or after the
  template: a hunt stops on yield (`templates.md`).
- Stage and resume texts (`STAGE`, `RESUME_HEAD`, `STAGE_DEFAULTS`, the amendment
  blocks): `worktrees.md`, *Staging*.

Template texts follow the preamble unchanged; a template item but a watch always gets the
full one, as do `audit` items and resume items (with *Resumed*). A **readonly** item in a
worktree, light or full, gets the readonly line in place of the worktree's commit and merge
paragraph and no leftover step; not a watch, the line adds that nothing merges, so ledger
entries and anything the prompt says to commit go in `carry:`. Since 146 (helium-hustle 045)
a readonly item's working papers go in `out/keep/<item>/` (its report and `carry:` lines name
them at their kept path, `kept.papers`, since 150), not the
committed results folder (full: `PAPERS_READONLY` in *The box*; light: `READONLY_LIGHT` on
the readonly line), and it skips the repo's warning checks, reporting `warnings: as on main`
(read as `none`) unless it ran a check its read depends on (`RUN_CHECKS_READONLY`).

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
- **Threads.** One budget (143): `{threads}`, `load.run_threads()` (the box's threads less
  `load.FREE_THREADS`, 4, and 2 of headroom), counting child processes. `load_high` logs under 2 free for ~3 min and messages only an
  item that is a real share of it; a Git Bash `nice -n 10` gives `BELOW_NORMAL` on Windows,
  inherited by children.
- **Scratch.** The daemon makes `out/<item>/` at launch; `out/keep/` stays shared and
  outlives the worktree (`worktrees.md`, *Kept reads*). `out/` is ignored before every
  launch and stage (`git.ignore_locally`: `/out/` in `.git/info/exclude`, never committed).
- **Warnings.** The header's `warnings:` line names the repo's CLAUDE.md *Warning checks*
  and its `warning_checks` (`executor.checks_text`; `warnings.md`).
- **Writable roots.** `write_roots` (`control-folder.md`; default `open_roots`) are named
  writable (`WRITES`) and the only-file line allows them; not readonly items or watches.
- **Doc budget** (151: fractals 023 measured ~20 cuts with `wc -c`, the bar only in the
  planner's digest). A writable item whose body (or template) names a trim, a doc or size
  budget, an `N KB` figure, or a repo doc over `doc_budget_kb` gets a *Doc budget* section
  after the body: which files count, the limit in bytes (1 KB = 1,024 here, as the digest
  measures), the repo's `doc_budget_exempt`, and each doc over it on main with its bytes; the
  report gives each trimmed doc's bytes before and after (`docbudget.section`).
