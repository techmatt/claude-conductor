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

The preamble (watch, light or full), its port and the scope, scratch and load rules every
executor is told: `preambles.md`.

**`wait_for`** (`mcp__conductor__wait_for`) replaces hand-written polls: 109 of
aicomplete 009's 281 turns were 60-second polls, about a third of its cost.

```
wait_for(path | pid | log + line [fail?, fresh?], pid?, every?, minutes?)    # every: 50 s; minutes: at most and by default 20
```

It returns when `path` exists, `pid` exits, or a line matching the regular expression
`line` is in `log` (lines already there count; a UTF-16 log reads fine), saying what
changed; a relative path is the working directory's (`--workdir`). With `fresh: true` only
lines this session's earlier `wait_for` on that log hasn't read count (a hit stops reading
just after its line), and on the first such call, only lines written after it: aicomplete
130's relaunched job's stale `RUN_DONE` ended three waits at once (84). A log that shrank
(rewritten) is read from its top again. A `log` wait also ends on
a line matching `fail` (default `panicked|error`, any case; `""` turns it off), `line` winning
on one line that matches both; the result says to narrow `fail` if the line is harmless, since
the same call would stop on it again. A `pid` beside `path` or `log` is the process doing the
work: its exit ends the wait with how things stand (aicomplete 051: a panic unseen for ~5 min,
waits on an exited script running out at 4 and 11 min). After `minutes` it says
how things stand: `path` not there, `pid` running, or `log`'s size, age and last line. The target
is checked every `every` seconds, the item's mailbox every second, so a message ends the
wait at once and the hook after the call delivers it; each mail file does that once, so a
helper's call can't spin (`ask`'s rule). `pid` is a Windows pid: Git Bash's `$!` is not one,
so a bash job waits on a `log` line or a marker `path`, or is launched with `start`. A `wait_for` counts as one tool call
and pings as `ask` does (*Long tool calls*). Code: `waitfor.py`, `ask_server.py`.

**`start`** (`mcp__conductor__start`, 106) launches a long job that must outlive its shell:
a Git Bash `&` (or `nohup`, or a `run_in_background` command) dies when Claude Code ends or
reaps that shell (aicomplete 188's farm). `start(command, log?)` runs `command` through Git
Bash (`procs.git_bash`: `CLAUDE_CODE_GIT_BASH_PATH`, else beside `git`, never System32's WSL
`bash`; `/bin/sh` elsewhere) via `procs.spawn_detached`: a launcher that exits at once, own
process group, out of the job object where allowed, below-normal priority, stdin closed, in
the working directory, output to `log` (default `out/<item>/<first word>-<HHMMSS>.log`). The log
is always fresh (116): a file already at that path (fractals 008's `out/browser.log` opened with
007's rows) moves aside to `<stem>.<MMDD-HHMMSS of its mtime><suffix>`, and the return says so.
It returns at once with the Windows pid for `wait_for`. Its cwd is the worktree and its
parent is gone, so the end-of-item reap (`procs.strays`) and the memory scan both count it.

The `--mcp-config` names one stdio server, `conductor`, run by the daemon's own Python
(`python -m conductor.ask_server --control … --item … --default-minutes … --session-id …
--workdir …`); its tools are `ask` (`questions.md`), `progress` (`progress.md`), `wait` (a
paused item), `wait_for` and `start`. The
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
a helper that sees it anyway to pass it up word for word. Since 110 it also says that a
message changing the executor's base (a rule, a card or a stage out or in) means a lever or
pick chosen on earlier reads gets one re-read on the new base before it ships, named in the
report: conductor can't tell which messages do, so every one carries it (aicomplete 201
shipped two picks read before Annihilate was taken out).

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
environment, so conductor can be started from inside a Claude Code terminal. So is
conductor's own venv (84, `procs.child_env`): `VIRTUAL_ENV` goes and PATH loses every
entry inside `sys.prefix`, since `uv run` put it first and an executor's plain `python` was
conductor's, without the repo's Pillow or ruff (fractal-website's `builder check` skipped
four checks, website 002 and 005). Conductor's own children name `sys.executable` outright.

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
