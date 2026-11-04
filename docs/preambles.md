# Preambles

What conductor tells an executor before the item body (`executor.py`); how it is launched,
its MCP and settings files, `wait_for` and message delivery: `executors.md`.

**Three preambles**, chosen by conductor, never by front-matter:

- **Watch** (`WATCH`, ~210 words) for `W<n>_watch` items: one
  `progress` line, scratch, short calls and `wait_for`, messages, the readonly line, the time
  limit and the report rules. No asking, outline, ETAs, port,
  load rules or leftover step.
- **Light** (`LIGHT`, ~380 words) for a small item (`executor.is_light`): a body under
  150 words (`LIGHT_WORDS`), none of `ultracode`, `template`, `resume`, `audit` or an
  explicit `max_hours`, and nothing that could produce candidates (`PICK_RE`, below: a
  short art round needs the picker schema). It keeps only what a
  small item can trip on: who it is and that nobody watches; ask with a default (it holds
  the session: background work first), block only if every default is worse; messages and amendments outrank the prompt and are
  answered in the report by number; short calls (nothing foreground over
  `foreground_seconds`, never end the turn to wait); the worktree, `paths`, readonly
  and *Beside you* rules (a shorter worktree text), and a one-line port rule where the
  repo can serve (`has_servers`, below); *Bounds*, one short
  paragraph of the rules below; the time limit; the report path, its header lines and
  what each status means. Progress is one line
  at the start, another only past ~15 minutes. No outline, ETAs, pickers or servers:
  an item that proves bigger may still use them, but nothing asks it to.
- **Full** (`PREAMBLE`) for everything else: who it is, unattended; *Asking and scope*
  (one section since 74: when to ask, always with a default; a call holds the session; block
  only when every default is worse; a skip binds; what isn't asked is decided and recorded;
  question defaults, `design: true` and an undoable step last, each said once; the leftover
  step); *Premises* and *The box* (below); progress and *Outline* (`progress.md`);
  *Messages* (`executors.md`, *Delivery*); *Short calls*; where it works, `paths`, *Beside you*
  (`scheduling.md`); the time limit; the **absolute report path** (the only file it may
  write outside its repo); the header and what each status means; the report budget (74:
  what changes for the planner first, header, findings, decisions, under ~60 lines, long
  tables linked from the results folder, a bug hunt's or test cleanup's required tables
  kept), every amendment and message answered by number, a confounded comparison flagged,
  test details only for an unfixed red, no narration; `Report written to <path>`.
  Three blocks go in only where they apply: *Pickers* and the header's `pick:` line
  (schema inline, `picks.md`) when the body could produce candidates (`executor.PICK_RE`:
  picker, `pick:`, candidates, alternatives, an `art:` command, the art pipeline or an art
  template); *Servers*, carrying the port (*Port*, below) and the 10-minute background
  limit (start detached, output to a log, stop before the end), when a run target's `cwd`
  is in the repo or a `package.json` at its root or one folder down (aicomplete's `web/`;
  74) has a `dev…` or `preview…` script (`executor.has_servers`); the *calibration* line
  (frozen numbers posted against the prompt's before the gate, a missed band's SE gap
  first) when the item is `rules: true` or names a gate, band, target or seed count
  (`executor.wants_calibrate`, 74).

Template texts follow the preamble unchanged; a template item but a watch always gets the
full one, as do `audit` items (with the *Audit notes* paragraph) and resume items (with
*Resumed*). A **readonly** item in a worktree, light or full, gets the readonly line in place
of the worktree's commit and merge paragraph, and no leftover step; a full one keeps *Asking and
scope*, *Outline*, *The box* and, where the repo serves, *Servers*, since a notes-only bug hunt
runs for hours and may serve the app.

**Running beside you now** ends the working-directory lines of a full or light preamble: the
project's other items executing or in setup, any repo, not paused, no watch, or "nothing"
(`talk.running_beside`, kept in the run record as `running_beside`), so the halve-threads rule
can be followed. An `alongside` item can start later (aicomplete 080 and 081 overloaded the
box), so on a change the next `progress` or `wait_for` return ends with the new line, once; the
full preamble says the latest return rules (74). Other projects' daemons don't show here;
*Now*'s load line does.

**Port.** Every launch (a resumed stage too) gets a TCP port of its own, chosen by
conductor: free at launch, from the OS's ephemeral range, never a run target's or tool's (the
ports in its `url` and `ready`), another live item's or the picker's. It is in the run
record and the `start` event (`port`), and in the executor's environment as
`CONDUCTOR_PORT`. Where the repo can serve (`has_servers`), the preamble says any server the
executor starts uses that port, passed through where the repo hard-codes one, so an item that
starts the app never collides with **run game** or another item's server; elsewhere it is
only in the environment (74). Stages and merge executors get none.

**Scope, scratch and load** (61, from aicomplete's 009, 030, 032 and 034). Both preambles say:

- **A skip binds.** A skip in the prompt, or one Matt sends by message, stands: the goal
  decides only routes the prompt didn't rule out. A skipped route it thinks best goes in
  its report or a question; its own default never un-skips it.
- **Question defaults.** A default may not contradict a prompt line or amendment unless the
  question's first line says `default overrides prompt: <line>` (`questions.md`).
- **Scratch** goes in `out/` in the working directory, never `%TEMP%` or anywhere outside
  it. Conductor makes `out/` ignored before every launch and stage (`executor.SCRATCH`,
  `git.ignore_locally`): unless a rule already ignores it, `/out/` goes into the repo's
  `.git/info/exclude`, local to the clone and shared by its worktrees, never committed and
  silent (a `warning` only if it fails; 72, after wordwarriors 172 and 175).
- **Scripts in files** (72; wordwarriors 174, aicomplete 074). A script is written with
  Write and run as a file, never a heredoc or `-c` string: an apostrophe broke quoted-delimiter
  heredocs in executor Bash.
- **Working papers** (73, aicomplete 079). Papers a prompt asks for (per-card notes,
  helper write-ups) go in a folder named for the item under the repo's results folder
  (`experiments/results/<item>/`), committed; ultracode helpers had put them in `out/`,
  git-ignored, and they were lost. Full preamble only.
- **Load budget.** Heavy compute (simulation sweeps, builds, test runs, subagent fan-out)
  runs at below-normal process priority and leaves at least 4 hardware threads free:
  simulation and test runs default to `load.run_threads()` threads (the box's less 4 and 2
  of headroom: 6 here; 73, after aicomplete #3405 ran at the 8 allowed and was killed beside
  outside processes), half that while another item runs alongside; at most 2 heavy
  subagents (builds, sims, browsers) at once. **This wins** over Workflow's "under 10 agents" and Ultracode's "cost is
  no constraint": light read-only reviewers and verifiers don't count
  against the 2, but run in batches per dimension. A Workflow runs at most 10 agents (or the
  prompt's number), finders and verifiers both, a hard count set before launch (68; `ultracode.md`).
  Not enforced: *Now*'s load line shows it broken, and `load_high` warns under 4 threads free (`memory.md`).
  The recipe (72, after wordwarriors 174): Git Bash `nice -n 10 <cmd>` gives `BELOW_NORMAL`
  on Windows, inherited by children (measured `0x4000` in both); the full preamble adds the
  PowerShell form.
- **Premises** (74, full only). A number or fact the prompt quotes is re-read from source
  before use, a moved one reported; a timing read beside another item reports only ratios.
- **`~/.claude`: config only** (74). Settings and permissions, never memory or a
  `CLAUDE.md`; a lesson goes in `carry:` for the repo's `CLAUDE.md`. Aicomplete 087 wrote a
  memory into `~/.claude/projects/C--Code-aicomplete/memory/`, which loads into sessions
  started in the main checkout (Matt's), not worktree executors or other projects.
- **Leftover budget** (75: a done-when check). Before finishing with over an hour left, the
  executor lists each done-when item against its measured result and takes a step that
  advances an unmet one, in scope, saying which (aicomplete 090 stopped 3 of 4 hours early
  on its band alone, two targets unmet). `max_hours` is a kill time, not a budget. A
  template's plainer form has no goal condition (`templates.md`).
