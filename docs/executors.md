# Executors

Each item runs as

```
claude -p --dangerously-skip-permissions --output-format stream-json --verbose --session-id <uuid>
       --mcp-config reports/<repo>__NNN_name.mcp.json --settings reports/<repo>__NNN_name.settings.json
```

with cwd = the item's worktree (the repo itself when `worktrees = false`) and the
prompt on stdin: a conductor preamble followed by the item body. A staged item is
resumed with `--resume <session id>` instead of `--session-id` (`worktrees.md`, *Staging*; a fork:
`resume.md`). The exact text sent is kept as `reports/<repo>__NNN_name.prompt.md`.

**Three preambles** (`executor.py`), chosen by conductor, never by front-matter:

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
  *Messages* (*Delivery*, below); *Short calls*; where it works, `paths`, *Beside you*
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
starts the app never collides with **run game** or another item's server; elsewhere the port
is only in the environment (74). A stage and a merge executor run no servers and get no port.

**Scope, scratch and load** (61, from aicomplete's 009, 030, 032 and 034). Both preambles say:

- **A skip binds.** A skip in the prompt, or one Matt sends by message, stands: the goal
  decides only routes the prompt didn't rule out. An executor that thinks a skipped route
  is best says so in its report or asks; its own question default never un-skips it.
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
- **Leftover budget.** Finishing with over an hour of the time limit left and the goal
  not fully met, the executor runs the next most useful step toward it, in scope, and says
  what it was. `max_hours` is a kill time, not a budget, so a goal fully met stops there.
  A template's plainer form has no goal condition (`templates.md`).

**`wait_for`** (`mcp__conductor__wait_for`) replaces hand-written polls: 109 of
aicomplete 009's 281 turns were 60-second polls, about a third of its cost.

```
wait_for(path | pid | log + line [fail?], pid?, every?, minutes?)    # every: 50 s; minutes: at most and by default 20
```

It returns when `path` exists, `pid` exits, or a line matching the regular expression
`line` is in `log` (lines already there count; a UTF-16 log reads fine), saying what
changed; a relative path is the working directory's (`--workdir`). A `log` wait also ends on
a line matching `fail` (default `panicked|error`, any case; `""` turns it off), `line` winning
on one line that matches both; the result says to narrow `fail` if the line is harmless, since
the same call would stop on it again. A `pid` beside `path` or `log` is the process doing the
work: its exit ends the wait with how things stand (aicomplete 051: a panic unseen for ~5 min,
waits on an exited script running out at 4 and 11 min). After `minutes` it says
how things stand: `path` not there, `pid` running, or `log`'s size, age and last line. The target
is checked every `every` seconds, the item's mailbox every second, so a message ends the
wait at once and the hook after the call delivers it; each mail file does that once, so a
helper's call can't spin (`ask`'s rule). `pid` is a Windows pid: Git Bash's `$!` is not one,
so a bash job waits on a `log` line or a marker `path`. A `wait_for` counts as one tool call
and pings as `ask` does (*Long tool calls*). Code: `waitfor.py`, `ask_server.py`.

The `--mcp-config` names one stdio server, `conductor`, run by the daemon's own Python
(`python -m conductor.ask_server --control … --item … --default-minutes … --session-id …
--workdir …`); its tools are `ask` (`questions.md`), `progress` (`progress.md`), `wait` (a
paused item) and `wait_for`. The
`--settings` file holds the PostToolUse message hook and the PreToolUse *Short calls* hook (`progress.md`), and
`"ultracode": true` for an `ultracode` item. A stage gets neither file.

**Delivery.** The message hook (`progress.md`) runs after every tool call, a
subagent's too, and delivers only on the lead's: Word Warriors 059 lost amendment #1500
to a helper that read it as injected text. Probed in 2.1.305 (`claude -p`, a hook
dumping its stdin): a subagent's call adds `agent_id` and `agent_type`; the lead's
(its `Agent` call included) has neither, and both share `session_id` and
`transcript_path`. A non-empty `agent_id` takes nothing and prints nothing: the mail
stays pending, no `delivered`, and the lead's next call delivers it
(`talk.subagent_call`). `agent_type` alone (`--agent` sessions) and unreadable input
count as the lead's. The `progress` tool hands out no mail: its server can't tell who
called, and the hook right after it can (`matcher: ""` covers MCP tools). The context
says it is Matt, the operator, through conductor's channel the preamble names, and tells
a helper that sees it anyway to pass it up word for word.

**Long tool calls.** Claude Code (2.1.301) aborts an MCP tool call that sends no
response or progress for 1800s, and `MCP_TOOL_TIMEOUT` does not change that. Two guards
keep a blocking ask alive, each proven alone with a 35-minute call: the server entry carries
`"timeout": 604800000` (7 days in ms), and the ask server sends
`notifications/progress` every 60s against the `progressToken` Claude Code puts in
every call's `_meta`. `"alwaysLoad": true` spares a ToolSearch turn (probed, 2.1.314).

Conductor generates the session id and passes it with `--session-id`, so the id is
known (in `.run.json` and the `start` event) before the executor writes a byte; phase 3
resumes sessions with it. Variables that bind a process to an enclosing Claude Code
session (`CLAUDECODE`, `CLAUDE_CODE_SESSION_ID`, …) are stripped from the executor's
environment, so conductor can be started from inside a Claude Code terminal.

stdout (stream-json, one JSON object per line, ending with a `"type": "result"` line)
goes to `reports/<repo>__NNN_name.log`; stderr to `.stderr.log`. Stream-json rather
than plain JSON, so a killed run still leaves its transcript.

**Setup.** A repo's `setup = [...]` commands run after a new worktree is created
(launch or stage) and after a staged worktree moves to current `main` on resume; never
for `worktrees = false`. They run off the daemon loop, in a runner process
(`python -m conductor.setup_run <spec.json>`, started like an executor: own process
group, no window, cwd `.conductor/` so a worktree's modules can't shadow `conductor`),
which the daemon polls each tick, so signals, requests, questions and other repos keep
moving. The runner runs each command in order in the worktree through the shell, each
limited to `setup_minutes`, appending output (its own too) to
`reports/<repo>__NNN_name.setup.log`, and writes its result JSON atomically. Meanwhile
the run record has `phase: setup` (a stage's record `setup.state: running`), the item
holds its repo and named locks, `setup` is logged, and status reads `setup NNN_name
…, pid <runner>` (the inbox's *Now*: `NNN_name (setting up)`; the app: `setting up`;
a stage in setup still shows `staging`). Success starts the executor (`start` or
`stage` gains `setup_seconds`). A failing command, a timeout, a runner that exits
without a result, or setup still running past `max_hours` (`stage_minutes` for a
stage) is a red launch error for a run (`setup 'npm ci' exited 1 (see
code__001_x.setup.log)`, worktree discarded) and discards a stage. Setup time counts
toward those deadlines. Code: `daemon_setup.py` (`SetupMixin`), `setup_run.py`.

The report it ends with, its header and the outcome: `reports.md`.
