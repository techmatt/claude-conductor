# Signals

The verbs: read, clear, land, retry, approve, cancel, reorder, review, reviewed, session_opened, tell,
outline, audit, audit_used, checkpoint, session, run, tool, pick, retire, open, notify, stop, restart, watch, push, autopilot, ack,
pause, resume, model, park.

Every change to the queue that is not a new item goes through `signals/`, so the daemon
carries it out between launches and logs it. The planner (or the app) writes a small
file (any name; `*.tmp` and `*.bad` are skipped) with `key: value` lines, applied in
order:

```
by: planner                  # optional: every event this file causes carries detail `by`
read: 42                     # inbox read through event #42 (`latest` or `all` = everything so far)
clear: sandbox               # release the sandbox queue after a red
land: sandbox/003            # land a finished red item's kept branch on main (worktrees.md)
retry: sandbox/003           # resume a finished red item's kept session in its kept worktree (api-retry.md)
approve: 007                 # proposed/007_x.md → queue/<its repo>/007_x.md
cancel: sandbox/004          # a queued item (or a proposed one: `cancel: 007`)
reorder: sandbox 006 004     # the listed items swap numbers; unlisted ones stay
review: all                  # write review.md: every unreviewed report inlined (or `review: sandbox/003`)
reviewed: sandbox/003        # the planner has acted on that finished item's report (or a comma list)
tell: sandbox/005 use sqlite # a message to running sandbox/005 (see progress.md)
outline: sandbox/005         # ask running sandbox/005 for an updated outline (a tell of fixed text)
audit_used: sandbox/003, 005 # those audit notes were handled already (`audit_used: sandbox/all`: every unused one)
checkpoint: mid-session      # a session boundary (see inbox.md)
session: start               # a checkpoint, then session.md, then the read mark moved (see inbox.md)
run: game                    # start run target `game` if needed, open its URL (`run: game stop`)
tool: cards                  # start project tool `cards` if needed, open its URL (`tool: cards stop`; app.md, Project tools)
pick: code/picks/art_alts_009.json   # serve that picker manifest and open it in the browser
retire: art_alts_009         # delete that picker's manifest and images (picks.md, Retiring)
open: reveal C:\Code\x\out.png   # a desktop action on a checked path (below)
open: file C:\Code\x\page.html?missing   # a local page in the browser, its query kept (open.md)
notify: ready when you are   # a desktop toast
stop: daemon                 # the daemon exits cleanly, as on Ctrl+C
restart: daemon              # the daemon restarts itself at its next safe point, adopting executors (restart.md)
watch: now                   # queue a watch item now (night-shift.md); refused while one is queued or running
push: sandbox                # push sandbox's main to its remote now (push.md); its push or push_failed follows
push: control                # the same for the control folder (when no repo is named control)
autopilot: night             # off | night | always (`on` = always): may the night shift start work itself (night-shift.md)
ack: invalid #2712           # that rejected signal is dealt with (plain `ack: invalid`: every one still live)
pause: sandbox               # its items commit, stop what they started and wait, clocks stopped; nothing new starts
resume: sandbox              # they carry on, deadlines moved by the pause; the queue is released (night-shift.md)
model: fable                 # opus | fable: every project's next new session (executors.md, Model)
```

One file may carry several lines; the planner's are `planner-<stamp>[-<n>].signal` (the MCP server adds the counter).
What became of each file is its receipt, `receipts/signals/<name>.md` (`receipts.md`).
Items are named `NNN_name` or `NNN` (numerically: `7` is `007`). A ` # comment` after a
value is ignored, except in `tell`, `checkpoint`, `session`, `notify`, `park` and `ack` (its `#<event>`).

- **approve** — the `proposed/` file must parse as a valid item whose `repo:` is
  configured, and `<repo>/NNN_name` must not already be queued, running, staged or done.
  It moves by one rename; `approve` is logged (item `<repo>/NNN_name`, `from`), then
  the usual `enqueue`.
- **cancel** — a name without a repo is looked up in `proposed/` first, then across
  every queue (a name found in two queues is ambiguous: refused). A running item is
  refused: cancel never kills an executor. A staged head has its stage discarded
  first (process tree killed, worktree removed, the usual `warning`). The file is
  moved, not deleted, to `.conductor/cancelled/<YYYYmmdd-HHMMSS>__<repo|proposed>__NNN_name.md`;
  `cancel` is logged with `from` (`queue`/`proposed`) and `kept`. A cancelled proposal's
  event has item `proposed/NNN_name` and the repo its front-matter names (or `proposed`
  when that repo is missing or not configured). A queued item whose `depends_on` names the
  cancelled one is listed on the event (`dependents`) and in the receipt's `warning`: each
  now holds by itself, its repo running on, until the name is requeued or it is cancelled
  (82; `scheduling.md`).
- **reorder** — `reorder: <repo|proposed> <item> ...`: the listed items take the sorted
  numbers they already hold, in the listed order (each number keeps its zero padding;
  the name after it is unchanged). Refused when an item is unknown, listed twice or
  running, when a new name is taken by an unlisted item, when a new queue name is the
  id of an item that ran or is running (`done/`, `running/`: ids are never reused),
  when any item in `queue/` or `proposed/` has a `depends_on` that names a renumbered
  item, when a renumbered queue item would come before an item of its own queue it
  depends on (it would hold forever), or while an earlier reorder is unfinished. A
  renumbered staged item loses its stage first. A renamed queue item is a new id, so
  it is enqueued again; amendments taken (`amended`) and amend files waiting beside it
  move with it; one written later under an old name waits, with a warning, for nothing.
  Renames go through temp names (`NNN_name.reorder~<new name>`), journalled in
  `.conductor/reorder.json`. A failed rename (a file held open) leaves the journal: a
  `warning`, then every tick rolls it forward until it completes (`reorder` with `note`),
  that queue launching nothing meanwhile; a restart finishes it the same way.
- **reviewed**, **review**, **session_opened** — marking a finished item's report reviewed, the
  `review.md` bundle, and a report's session opened: `reports.md`, *Reviewing*.
- **tell** — `tell: <item> <message>`: `progress.md` (refused in setup). It never answers
  a question the item waits on (`questions.md`).
- **pause** / **resume** — `pause: <repo>`, `resume: <repo>` (a no-op when already so, 129):
  `night-shift.md`, *Pause*.
- **outline** — `outline: <item>`: a tell of the fixed outline request; see `progress.md`,
  *Outline*.
- **land** — `land: <item>` (a finished item, resolved as for `reviewed`): its kept branch
  lands on `main` as one commit, and the hold its red put on the queue is released; see
  `worktrees.md`, *Landing a kept branch*.
- **retry** — `retry: <item>` (a finished red item, resolved as for `reviewed`): its session
  resumes in its kept worktree and the item finishes, merges and reports as usual; a non-red
  finish clears its red's hold; `retry: <item> model: opus` moves it to that model (144).
- **park** — `park: <repo> <line>` adds a parked edit; `… remove <piece>` / `… replace
  <piece> => <new line>` changes the one waiting line holding the piece (144, `parked.md`).
- **audit** — `audit: <item>` on a running item (resolved as for `tell`): conductor's own
  *Audit notes* text (`templates/audit.md`, the one `audit: true` gives at launch, with the
  item's notes path) sent as a tell marked `audit: true`; its run record gains `audit`, so
  its `finish` carries `audit: true` and the next cleanup reads its notes. Refused for an
  item not running, or one already keeping notes. A queued item takes `audit: true` in its
  front-matter instead (`templates.md`).
- **audit_used** — `audit_used: <item>[, <item> …]` (each resolved as for `reviewed`) or
  `<repo>/all` (every unused note of the repo at that moment): notes an item already handled,
  so they leave *audit pending* and no cleanup is handed them. Logs one `audit_used` per repo
  with no item (`by`). Refused, with nothing marked, for an item with no notes or one
  claimed by a running cleanup; notes already used, or a repo with none unused, are a
  no-op (129).
- **checkpoint** — `checkpoint: <label>` (any text, a ` # ` included, or none): see
  `inbox.md`.
- **session** — `session: <label>` (any text, or none): a checkpoint with that label,
  then `session.md`, then a `read` past it; see `inbox.md`, *Session start digest*.
- **run** — `run: <target>` or `run: <target> stop`: see `run-targets.md`. An unknown
  target is refused.
- **tool** — `tool: <name>` or `tool: <name> stop`: what the Tools menu's entries do; see
  `app.md`, *Project tools*. A running tool only opens. An unknown tool is refused.
- **pick** — `pick: <repo>/<manifest path>`: see `picks.md`. The value is one word (a
  manifest path with a space, or ` #`, cannot be named) and its repo must be configured,
  or the file is rejected at parse time; a manifest that fails validation rejects it
  when applied, with the reason.
- **retire** — `retire: <picker name>`: deletes that picker's manifest and images; see
  `picks.md`, *Retiring*. An unknown name is refused; an already retired one is a no-op.
- **open** — `open: <verb> <path or URL>`: one named desktop action on a checked path
  (`folder`, `reveal`, `file`, `text`, `url`); see `open.md`.
- **notify** — `notify: <text>`: a desktop toast; see `open.md`.
- **stop: daemon** — the daemon finishes the signals of that tick, logs `daemon_stop`
  (`reason: signal`), releases its lock and exits. Executors and stages keep running;
  the next start adopts them.
- **restart: daemon** (71) — "restart all": the daemon restarts itself as on a code change
  (`restart.md`, *Asked restarts*), on the code on disk, so executors and stages are adopted
  and the tools keep running. A `stop: daemon` beside it wins.
- **autopilot** — `autopilot: off|night|always` (`on` is `always`): logs `autopilot`
  (mode, was), which every later decision reads (`night-shift.md`, *Autopilot*).
- **ack** — `ack: invalid [#<event> …]` (none named: every live one) logs `ack`; those
  rejected signals leave the app's "N rejected" and the inbox (`inbox.md`), not Events.
  An id already clear (acknowledged, past a checkpoint, never a rejection), or a plain
  `ack: invalid` with none live, is a no-op: `nothing to acknowledge …` in the receipt,
  no event (129). The app's **Dismiss** sends `ack: invalid`.
- **model** (135) — `model: opus|fable` writes the machine's setting and logs `model`
  (model, model_id, was); the receipt: `model is now Fable (claude-fable-5-2) …`.

**Tidy-ups** (129): a line whose wish already holds logs nothing (so no notice) and is
named in the receipt's `result`: `ack:` of a clear id, `reviewed:`, `retire:`,
`audit_used:` again (or `<repo>/all` with none), `pause:` when paused, `resume:` when not,
`model:` as set.
(`run|tool: X stop` of one not running logs `run_stop` with a note.) Still refused: a
wrong thing (unknown, or not finished).

**Refusals end with the accepted form** (129): an `invalid` error (the receipt's
`reason`) ends `; use <form>`, the failing key's (`daemon_signals.FORMS`) or, for an
unknown key, the nearest known keys'; an `open_refused` reason, its own (`open.md`).

Conductor processes a file once it reads and has stopped changing (a `planner-*` one at
0.25s, any other at 1s: `receipts.md`, *When*), then deletes it. The whole file is syntax-checked first: an unknown key or malformed line rejects it with
nothing applied. Each verb then checks what it acts on before changing anything; a
verb that fails there stops the file, and the lines before it stay applied (the
`invalid` event lists them as `applied`). A rejected file is logged `invalid` and
renamed `<name>.bad`. While a file's lines apply, `.conductor/signal.json` records it
and the lines done so far; if the daemon dies before the file is gone, the next start
sets the file aside as `.bad` and logs a `warning` naming what applied, so no line
ever applies twice (re-send what is still wanted). The read mark only moves forward. Use the id printed at the top
of `inbox.md` so events that arrived after reading stay unread.

Files are applied in name order. The app writes
`signals/app-<YYYYmmdd-HHMMSS>-<nnnnnn>.tmp` (a zero-padded counter, so clicks in one
second keep their order), then renames it to `.signal`, always with `by: app`.
