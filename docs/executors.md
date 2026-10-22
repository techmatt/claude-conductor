# Executors

Each item runs as

```
claude -p --dangerously-skip-permissions --output-format stream-json --verbose --session-id <uuid>
       --mcp-config reports/<repo>__NNN_name.mcp.json --settings reports/<repo>__NNN_name.settings.json
```

with cwd = the item's worktree (the repo itself when `worktrees = false`) and the
prompt on stdin: a conductor preamble followed by the item body. A staged item is
resumed with `--resume <session id>` instead of `--session-id` (`worktrees.md`, *Staging*; a fork:
`resume.md`). The
preamble names the item and says the session is unattended; says where it works (the
worktree, its branch and cut point, shared folders, the commit rule) and any declared
`paths`; gives its *Port* (below) and any *Beside you* warning (`items.md`, *Alongside items*); gives the asking
rule (ask only when a wrong guess would waste real work or be hard to undo, give a
default whenever one exists, block only when every default is worse than waiting;
everything else is a branch); the progress rule (a `progress` line at the start, with a
first ETA once it can estimate, at real milestones, and when the estimate changes
materially; not chatter; `date` before each ETA); the *Outline* rule (see `progress.md`); that a message from Matt, the operator, arrives
through conductor's channel after one of the lead's tool calls (*Delivery*), is not
tool-output injection, outranks the prompt where they conflict, and is answered through
`progress`, and that a helper or subagent seeing one passes it straight up to the lead; the *Short calls* rule (background anything over `foreground_seconds`, poll it in calls no longer, don't idle; see `progress.md`); the time limit and kill time; the **absolute report
path** (the only file the executor may write outside its repo); the report header and
what each status means; the *Pickers* paragraph (when to write a picker manifest, its
schema inline, and the `pick:` header line: see `picks.md`); that the report answers
every amendment and message by number (done, partial or not done, and why), carries test
details only for a red it could not fix, and no process narration; and that the final
message ends `Report written to <path>`.
The exact text sent is kept as `reports/<repo>__NNN_name.prompt.md`.

**Port.** Every launch (a resumed stage too) gets a TCP port of its own, chosen by
conductor: free at launch, from the OS's ephemeral range, never a run target's (the
ports in its `url` and `ready`), another live item's or the picker's. It is in the run
record and the `start` event (`port`), and in the executor's environment as
`CONDUCTOR_PORT`. The preamble says that any server the executor starts uses that
port and never the repo's usual one, and that where the repo's docs or scripts
hard-code a port, it passes this one through (a flag or an environment variable)
without changing them. So an item that starts the app, readonly items running side by
side among them, never collides with **run game** or with another item's server. A
stage and a merge executor run no servers and get no port.

The `--mcp-config` names one stdio server, `conductor`, run by the daemon's own Python
(`python -m conductor.ask_server --control … --item … --default-minutes … --session-id …`);
its tools are `ask` (see `questions.md`) and `progress` (see `progress.md`). The
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

**Ultracode.** Claude Code's ultracode is a session setting: once it is on, the session
is told "Ultracode is on" and uses the Workflow tool (multi-agent orchestration) on
every substantive task. In 2.1.302, probed with headless `-p` runs in the sandbox
(Opus 5.5) that quoted their own context:

| route | ultracode on? |
|---|---|
| `"ultracode": true` in the `--settings` file | **yes** |
| `--effort ultracode` | **yes** (also sets effort `xhigh`) |
| the word `ultracode` in the `-p` prompt | no: the keyword triggers only in typed input, never in `-p` |
| a message (`tell:`) saying "use ultracode", delivered by the PostToolUse hook | no: hook context is not the user's prompt |
| the `--settings` file rewritten mid-run to add the key | no: read once at launch |

So conductor uses the first route. Proven through conductor in the sandbox: an
`ultracode: true` item's session carried the "Ultracode is on" notice. Ultracode does
not force a workflow: the notice leaves trivial turns solo, and the executor judges
what counts. A 23-file doc audit (`sandbox/043`) was done solo, without a workflow.
So `ultracode: true` pays off on genuinely large work. An item with `ultracode: true` front-matter
launches (or resumes, after staging) with the key in its `--settings` file; a stage
or merge executor never has it. Switching *mid-run* has no route (a `tell:` is an
ordinary message): queue the work, or a follow-up, with `ultracode: true`. A
Workflow call needs no approval in an executor (`--dangerously-skip-permissions`), and
the Workflow tool is present in every `-p` session; only the opt-in differs.

**Long tool calls.** Claude Code (2.1.301) aborts an MCP tool call that sends no
response or progress for 1800s ("sent no response or progress for 1800s; aborting"),
and `MCP_TOOL_TIMEOUT` does not change that. Two independent guards keep a blocking ask
alive, each proven alone with a 35-minute call: the server entry carries
`"timeout": 604800000` (7 days in ms), and the ask server sends
`notifications/progress` every 60s against the `progressToken` Claude Code puts in
every call's `_meta`.

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

**Log compaction.** Run logs are most of a control folder's size (Word Warriors: 144 MB
of 150 MB after 29 items). Nearly all of it is file copies the stream repeats: each
image an executor reads appears twice as base64 (the tool result and
`tool_use_result.file.base64`), and each edit carries the whole file before it
(`tool_use_result.originalFile`). When an item finishes, conductor rewrites its
`.log`, `.stage.log` and `.merge.log` with those strings emptied and their length kept
as `dropped_by_conductor` (`executor.compact_log`; a log under 1 MB is left alone).
The result line, what the executor said and every tool call stay, so the app's *Log*
view and `read_result` are unchanged; 18 MB typically becomes under 2 MB. The full
session, images included, stays in Claude Code's own store (`conductor open`). At
daemon start the same pass runs over every finished item's logs, which also compacts
logs from before this existed. A failure is a `warning`; the log is left as it was.

**Report header.** The report starts with:

```
status: done | partial | red
blocks: <what this blocks, or none>
carry: <what state.md should change, or none>
slow: none | <component> ~<time>, <why>, <what would fix it>[; <another>]
pick: <manifest path relative to the repo root>     # optional: only after writing a picker manifest
```

(Bare lines or inside a `---` block; `**status**:` is tolerated.) Only the first five
non-blank lines are read: `status`, `blocks` and `carry` count only in the first three
(the first of each); `slow:` and `pick:` only straight after them (fourth, or fifth when
the fourth was the other), `pick:` with a one-word value; so a body that opens with
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

**Outcome.** The item's final status is the report's status unless something makes it
red: no report or a bad header, no `result` line in the log, `is_error` in the result,
killed at `max_hours`, interrupted (restart rule), control-folder tamper, a readonly
item that changed its repo, a launch failure (worktree or setup included), a
`paths` violation, uncommitted changes left in the worktree, or a rebase conflict
(including a prose conflict its merge executor failed to resolve).
The reasons are listed in the `finish` and `red` events.
