# Progress and messages

**Progress.** The executor's second tool, `progress` (`mcp__conductor__progress`):

```
progress(note, eta?, done?, remaining?, outline?)   # eta: free text (5:40pm, 25m); done/remaining: numbers
                                                    # outline: [{title, eta, done}], see Outline
```

It returns at once (`Logged; carry on.`). The ask server appends a line (`seq`, `at`,
`note` — whitespace collapsed, 500 chars — and the rest) to
`.conductor/progress/<repo>__<NNN_name>.jsonl`; the daemon logs one `progress` event
per new line (`seq` dedupes across restarts). The latest line of a running item shows
in the inbox's *Now*, `conductor status`, the app's Repos tab and the Activity view;
`progress` events are left out of the inbox's *Other* section.

**Outline.** `progress` also takes `outline`: the item's sections in order, the report
last, each `{title, eta, done}` (a few words, a local clock time, a bool; ~8 rows asked,
12 taken). With an outline, `note` may be left out. It is stored on the progress line
and logged on the `progress` event (`outline`); the newest one per item is its current
outline (`inbox.latest_outline`). The preamble's *Outline* rule: post one once oriented
with a plan, again whenever a message asks, and resend it with a row `done` when that
section finishes, re-estimating the others only if they moved a lot; coarse, no
descriptions, no effort on accuracy. A staging session ends its plan with the
outline's sections and posts it first thing after resume (a stage has no `progress`
tool). One line, `outline 2/5 · next: Re-cut images by 11:05am · finish ~11:20am
(posted 10:52am)` (`inbox.outline_line`; *next* is the first undone row, *finish* the
last row's time), stands after the progress note on the inbox's *Now* line (alone
when the latest line was only an outline) and under the item in `conductor status`.
The **request** is `outline: <item>` (the app's **Outline?**, `conductor outline
<item>`): a `tell` of the fixed text `talk.OUTLINE_REQUEST` ("Post an updated outline
now: …"), logged with `outline_request: true`, refused exactly as a tell is, and
delivered like one, so its reply tag pairs it with the outline it produced.

**Messages.** `tell: <item> <message>` is a signal verb (the app's **Tell…**, `conductor
tell` — which writes `signals/cli-….signal` with `by: cli` — and the planner all use
it). The message is the rest of the line, ` # ` included (it is not a comment there);
the app joins a multi-line message with ` / `. It applies only to an executing item
(`running/`, phase `exec`, process not exited): a queued, staged or in-setup item is
refused as not started, a merging or finished one as exited, an unknown one as no running item.
The daemon logs `tell` (text, by) and writes `.conductor/mail/<repo>__<NNN_name>/<tell
id>.json`.

Delivery is a Claude Code **PostToolUse hook**. The executor's `--settings` file
(`reports/<repo>__NNN_name.settings.json`) names one hook on every tool (`matcher: ""`):
`<python> -m conductor.talk hook --control … --item …` (the `command` + `args` form, so
no shell quoting; 15s timeout). After each tool call it claims every waiting message
by renaming `<id>.json` → `<id>.delivered.json` and prints
`{"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": …}}`:
`MESSAGE #<id> from Matt via <by> …`, the text, and that it is an instruction from
Matt that wins over the prompt and is answered with `progress`. With no mail it prints
nothing. A `progress` call also hands over any message still waiting (the rename makes
either path deliver it once). The claim also records `after_seq`, how many progress
lines the item had written by then. The daemon logs `delivered` (tell, via `hook` or
`progress`, text, waited_seconds, after_seq), and tags the item's first progress line
with a higher `seq` as the **reply** (`reply_to: <tell id>`; a list when several
messages reached it before the same line). A message handed over by a `progress` call
comes after that call's own line, so its reply is the next one. A running amendment
(via `hook` or `progress`) is tagged the same way; one delivered at launch or resume is
not. The daemon logs deliveries before progress lines in each pass, and rebuilds what
still waits for a reply from the log at start. `conductor events`, the inbox and the
Activity view show a tagged line as `progress (reply to #N)`; the app's Repos tab shows
the reply under its tell (`app-panels.md`, Conversation). An item that exits with messages still waiting has
them renamed `.undelivered.json`, and a `warning` names each.

Proven with Claude Code 2.1.301 headless (`-p`, stream-json): a message sent while a
real executor worked through a run of `sleep 25` Bash calls reached it after the call
in progress, and it changed course (see `reports/07_heads_report.md`). The injected
context is not written to the stream-json log; the `delivered` event is the record.
The user's own `~/.claude/settings.json` hooks still run beside it (`--settings` adds
to them).

**Short calls.** Since a message only gets in between tool calls, conductor keeps
every executor tool call to about a minute, the headless stand-in for pressing Ctrl+B
in an interactive session. Word Warriors 034 shows why: a tell waited 461 s because
the executor, having rightly backgrounded its test run, waited on it with one
foreground `timeout 590 bash -c 'until grep -q EXIT …; do sleep 10; done'` call; that
run made 20 such calls of 1–10 minutes, about 113 minutes in all. Two parts:

- The preamble's *Short calls* paragraph: run anything that may take over
  `foreground_seconds` (default 60) with `run_in_background`, wait on it in polls of at
  most that long, and don't end the turn to wait (an idle session takes no message).
- A **PreToolUse hook** on `Bash|PowerShell` in the same `--settings` file:
  `<python> -m conductor.talk pre --cap <foreground_seconds> --background=<glob> …`.
  A call already `run_in_background` is left alone. A command matching one of the
  repo's `background` globs (`fnmatch` on the whole command with runs of whitespace
  collapsed, case-sensitive, `*` crossing spaces: `npm test*`, `*playwright test*`)
  gets `run_in_background: true` and loses its `timeout`. Any other call whose
  `timeout` is missing (Claude Code's default is 2 minutes) or longer than the cap gets
  `timeout` = the cap. It prints `{"hookSpecificOutput": {"hookEventName":
  "PreToolUse", "permissionDecision": "allow", "updatedInput": …}}`, or nothing.
  `foreground_seconds = 0` with no globs drops the hook and the paragraph.

What makes the cap safe (Claude Code 2.1.304, `-p`): a foreground Bash call that
reaches its `timeout` is not killed but **moved to the background** ("Command did not
complete within its 60s timeout and was moved to the background (ID: …)"), the tool
call ends there, and the session is notified when the command finishes, as for one
started in the background; `-p` keeps the session alive until its background commands
end. A command moved this way is killed 10 minutes after the move (Claude Code says
so in the result; measured: moved at 15 s, killed at 10m15s); one started with
`run_in_background` has no such limit, which is what the `background` globs are for: a
suite that can run longer.

Proven in the sandbox with real executors (2.1.304) and a 4-minute stand-in suite, a
tell sent mid-run each time: told only by the preamble, the executor backgrounded the
suite and polled in 55 s calls, and the tell landed in 21 s (sandbox 047); told by its
prompt to run the suite as one foreground call with `timeout` 600000, the hook cut it
to 60 s, the call came back after ~73 s as moved, the tell landed in 52 s, and the
executor polled the moved command's output to the end (048). Both reported the
suite's summary and failing test correctly and acted on the tell. The 60 s is a
`timeout`, so a call can run a little past it (spawn and hook time under load).

**Amendments.** An amendment adds to an item's instructions without rewriting its file,
in any state but finished. The planner writes `queue/<repo>/NNN_name.amend.md` beside
the item (the item may be queued, staged or already in `running/`; the amend file
always goes in `queue/`), or any `NNN_name.amend.<anything>.md` (a timestamp, so no
need to check for an existing one), taken in name order, numbers as numbers:
`.amend.md` < `.amend.2.md` < `.amend.10.md` < `.amend.20261020-1530.md`. Optional front-matter carries `by:` (`by: planner`; none is `unknown`); the
body is the amendment, any length. The app's **Amend…** and `conductor amend <item>
<text…|file>` (one argument naming a file sends its contents; `by: cli`) write the next
free name, as `.tmp` then renamed. An amend file is never an item: no `enqueue`, no
`edited`, and a stage is kept. Each tick, after enqueues and stage checks, the daemon
takes every amend file at least `SIGNAL_SETTLE_SECONDS` old (an item whose amend file is
younger waits a tick before it launches or resumes), logs `amended` (text, by, state,
delivery; its id is the amendment's number) and moves it into the item's mailbox
(`.conductor/mail/…/<id>.json`, `kind: amend`). Delivery by state:

- **queued** (not staged; or in setup): appended to the prompt at launch, each under `## Amendment #n
  (5:25pm, via planner): it amends the prompt above and wins where they conflict`.
- **staging / staged** (a staged resume in setup too): the stage is kept; at resume the amendments follow the resume
  message, which tells the executor each amends the prompt it read and overrides the
  plan it made where they conflict.
- **running**: through the message hook, whole (multi-line), as `AMENDMENT #n from
  Matt via <by>, an addition to the prompt you are running …` with a tell's closing
  wording.
- **merging / finished**: refused (a correction to finished work is a new item):
  `amend_refused` (red: inbox, toast) with the reason, the file left where it is and
  refused once per content (a changed file is judged again). An amend file naming no
  item yet logs one `warning` and waits; it is taken once the item appears, so an amend
  file written with its item still lands at launch.

`delivered` follows each (amend, via `prompt`, `resume`, `hook` or `progress`), and
every amendment is appended to `reports/<repo>__NNN_name.amend.md` as delivered: number,
when amended, by, state, when delivered and via, and the text, so a postmortem shows
what the executor was told and when. One the executor exited before taking is marked
there as never delivered, with a `warning`. A reorder carries an item's waiting
amendments to its new name.

**Limits:** a message still arrives only after a tool call ends: not while a blocking
`ask` waits, not while the executor sits idle at the end of a turn waiting for a
background command's notification (the preamble asks it not to), and never once it
has made its last tool call (then it is undelivered). A non-shell tool that runs long
(an Agent / Task subagent, a Workflow) is not capped.

**Open a finished session.** `conductor open <item>` (and **Terminal** in the app's
Repos tab, **Open in terminal** in its trail) opens a new console window running `<executor command> --resume <session id>`
(the session id from the item's `finish` event) in the item's worktree while it exists,
else the repo's main checkout: `--resume` finds a session from any folder. Refused
while the item is running or staged (resuming a live session forks it). Windows only;
elsewhere the error prints the command to run.
