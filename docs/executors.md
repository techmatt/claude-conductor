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
  `foreground_seconds`, never end the turn to wait); the worktree, `paths`, readonly,
  port and *Beside you* rules (shorter worktree and port texts); *Bounds*, one short
  paragraph of the rules below; the time limit; the report path, its header lines and
  what each status means. Progress is one line
  at the start, another only past ~15 minutes. No outline, ETAs, pickers or servers:
  an item that proves bigger may still use them, but nothing asks it to.
- **Full** (`PREAMBLE`) for everything else. It names the item and says the session is
  unattended; gives the asking rule (ask only when a wrong guess would waste real work,
  be hard to undo, or the result rests on a reading of a scope line, with a default whenever one exists; a call holds the session, so background work starts first; block only when every default
  is worse; anything not asked is decided as fits the goal and recorded; a step whose
  default can't be undone goes last); the progress rule (a line at the start, an ETA once
  it can estimate, at milestones, when the estimate moves by a third; `date` before each
  ETA); the *Outline* rule (`progress.md`); *Scope* and *The box* (below); the *Messages* rule
  (*Delivery*, below); the *Short calls* rule
  (`progress.md`); where it works, any `paths`, *Port* (below), *Beside you*
  (`scheduling.md`); the time limit and kill time; the **absolute report path** (the only file it may write outside
  its repo); the header, a one-line `slow:` rule and what each status means; that the
  report answers every amendment and message by number, flags a conclusion resting on a comparison that changed two things, carries test details only for an
  unfixed red, and no process narration; and the closing `Report written to <path>`.
  Two blocks go in only where they apply: the *Pickers* paragraph and the header's
  `pick:` line (schema inline, `picks.md`) when the body could produce candidates
  (`executor.PICK_RE`: picker, `pick:`, candidates, alternatives, an `art:` command,
  the art pipeline or an art template); the *Servers* paragraph (Claude Code stops a
  background command after 10 minutes, so a long-lived server starts detached with its
  output to a log, or by a repo script, and stops before the end) when a run target's
  `cwd` is in the repo or its `package.json` has a `dev…` or `preview…` script
  (`executor.has_servers`).

Template texts follow the preamble unchanged; a template item but a watch always gets the
full one, as do `audit` items (with the *Audit notes* paragraph) and resume items (with
*Resumed*). A **readonly** item in a worktree, light or full, gets the readonly line in place
of the worktree's commit and merge paragraph, and no leftover step; a full one keeps *Asking*,
*Outline*, *The box* and *Port*, since a notes-only bug hunt runs for hours and may serve the app.

**Running beside you now** ends the working-directory lines of a full or light preamble: the
project's other items executing or in setup, any repo, not paused, no watch, or "nothing"
(`talk.running_beside`, kept in the run record as `running_beside`), so the halve-threads rule
can be followed. When that changes, the next `progress` call's return says so. Other projects'
daemons don't show here; *Now*'s load line does.

**Port.** Every launch (a resumed stage too) gets a TCP port of its own, chosen by
conductor: free at launch, from the OS's ephemeral range, never a run target's or tool's (the
ports in its `url` and `ready`), another live item's or the picker's. It is in the run
record and the `start` event (`port`), and in the executor's environment as
`CONDUCTOR_PORT`. The preamble says any server the executor starts uses that port, passed
through where the repo hard-codes one, so an item that starts the app never collides with
**run game** or another item's server. A stage and a merge executor run no servers and get
no port.

**Scope, scratch and load** (61, from aicomplete's 009, 030, 032 and 034). Both preambles say:

- **A skip binds.** A skip in the prompt, or one Matt sends by message, stands: the goal
  decides only routes the prompt didn't rule out. An executor that thinks a skipped route
  is best says so in its report or asks; its own question default never un-skips it.
- **Question defaults.** A default may not contradict a prompt line or amendment unless the
  question's first line says `default overrides prompt: <line>` (`questions.md`).
- **Scratch** goes in `out/` or another git-ignored folder in the working directory, never
  `%TEMP%` or anywhere outside it.
- **Load budget.** Heavy compute (simulation sweeps, builds, test runs, subagent fan-out)
  runs at below-normal process priority and leaves at least 4 hardware threads free; at
  most 2 heavy subagents (builds, sims, browsers) at once; thread counts halve while another
  item runs alongside. **This wins** over Workflow's "under 10 agents" and Ultracode's "cost is
  no constraint": light read-only reviewers and verifiers don't count
  against the 2, but run in batches per dimension. A Workflow runs at most 10 agents (or the
  prompt's number), finders and verifiers both, a hard count set before launch (68; `ultracode.md`).
  A rule, not enforced: the planner sees it broken on *Now*'s load line, and under 4 threads free `load_high` warns (`memory.md`).
  The full preamble gives the PowerShell form, `(Start-Process … -PassThru).PriorityClass = 'BelowNormal'`.
- **Global Claude config.** Editing `~/.claude` is allowed when it's a good idea; each
  edit is named in the report, with its reason.
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
