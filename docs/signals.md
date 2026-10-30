# Signals

The verbs: read, clear, land, approve, cancel, reorder, review, reviewed, session_opened, tell,
outline, audit, checkpoint, session, run, pick, retire, open, notify, stop, watch, push, autopilot, ack,
pause, resume.

Every change to the queue that is not a new item goes through `signals/`, so the daemon
carries it out between launches and logs it. The planner (or the app) writes a small
file (any name; `*.tmp` and `*.bad` are skipped) with `key: value` lines, applied in
order:

```
by: planner                  # optional: every event this file causes carries detail `by`
read: 42                     # inbox read through event #42 (`latest` or `all` = everything so far)
clear: sandbox               # release the sandbox queue after a red
land: sandbox/003            # land a finished red item's kept branch on main (worktrees.md)
approve: 007                 # proposed/007_x.md → queue/<its repo>/007_x.md
cancel: sandbox/004          # a queued item (or a proposed one: `cancel: 007`)
reorder: sandbox 006 004     # the listed items swap numbers; unlisted ones stay
review: all                  # write review.md: every unreviewed report inlined (or `review: sandbox/003`)
reviewed: sandbox/003        # the planner has acted on that finished item's report
tell: sandbox/005 use sqlite # a message to running sandbox/005 (see progress.md)
outline: sandbox/005         # ask running sandbox/005 for an updated outline (a tell of fixed text)
checkpoint: mid-session      # a session boundary (see inbox.md)
session: start               # a checkpoint, then session.md, then the read mark moved (see inbox.md)
run: game                    # start run target `game` if needed, open its URL (`run: game stop`)
pick: code/picks/art_alts_009.json   # serve that picker manifest and open it in the browser
retire: art_alts_009         # delete that picker's manifest and images (picks.md, Retiring)
open: reveal C:\Code\x\out.png   # a desktop action on a checked path (below)
notify: ready when you are   # a desktop toast
stop: daemon                 # the daemon exits cleanly, as on Ctrl+C
watch: now                   # queue a watch item now (night-shift.md); refused while one is queued or running
push: sandbox                # push sandbox's main to its remote now (push.md); its push or push_failed follows
push: control                # the same for the control folder (when no repo is named control)
autopilot: night             # off | night | always (`on` = always): may the night shift start work itself (night-shift.md)
ack: invalid #2712           # that rejected signal is dealt with (plain `ack: invalid`: every one still live)
pause: sandbox               # its items commit, stop what they started and wait, clocks stopped; nothing new starts
resume: sandbox              # they carry on, deadlines moved by the pause; the queue is released (night-shift.md)
```

One file may carry several lines; the planner names its files `planner-<stamp>.signal`.
What became of each file is its receipt, `receipts/signals/<name>.md` (`receipts.md`).
Items are named `NNN_name` or `NNN` (numerically: `7` is `007`). A ` # comment` after a
value is ignored, except in `tell`, `checkpoint`, `session`, `notify` and `ack` (its `#<event>`).

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
  when that repo is missing or not configured).
- **reorder** — `reorder: <repo|proposed> <item> ...`: the listed items take the sorted
  numbers they already hold, in the listed order (each number keeps its zero padding;
  the name after it is unchanged). Refused when an item is unknown, listed twice or
  running, when a new name is taken by an unlisted item, when a new queue name is the
  id of an item that ran or is running (`done/`, `running/`: ids are never reused),
  when any item in `queue/` or `proposed/` has a `depends_on` that names a renumbered
  item, when a renumbered queue item would come before an item of its own queue it
  depends on (it would hold forever), or while an earlier reorder is unfinished. A
  renumbered staged item loses its stage first. A renamed queue item is a new id, so
  it is enqueued again; amendments already taken (`amended`) move with it, and amend
  files still waiting beside it (`old.amend.x.md`) are renamed with it (`new.amend.x.md`).
  An amend file written later under an old name waits with a warning for an item that
  never comes. Renames go through temp names (`NNN_name.reorder~<new name>`,
  not `*.md`), journalled in `.conductor/reorder.json`. A rename that fails (a file
  held open) leaves the journal: a `warning` is logged, every tick rolls it forward
  until it completes (`reorder` logged with `note`), and that queue launches nothing
  meanwhile. A restart finishes an interrupted reorder the same way.
- **reviewed** — `reviewed: <repo>/<NNN_name>` (or `NNN_name`, `NNN`; a name without a
  repo is looked up across every repo, and one found in two is ambiguous: refused).
  It marks one finished item's report as reviewed and logs `reviewed` (item, `finish`:
  the finish event's id, `status`, `by`: the file's `by:` or `unknown`). Refused for an
  item that has not finished (queued, staged or running) or is unknown. An item already
  reviewed is a no-op: a `warning` names the earlier `reviewed` event and the file is
  not rejected. Reviewed state is derived from the event log (an item is reviewed once a
  `reviewed` event names it), so it survives restarts, and it is independent of the
  read mark: reading the inbox never marks anything reviewed, and `needs_planner` still
  waits on the read mark. Several `reviewed:` lines may share a file. The event keeps
  `hashes` of the report and its `.audit.md`: when either changes later, the daemon's
  scan (and its first, at start) logs `report_changed` and the item is unreviewed again
  until the next `reviewed:`, which is then accepted (`inbox.md`, *Reviewed reports
  that change*). A report reviewed before hashes were kept is never checked.
- **session_opened** — `session_opened: <item>` (one finished item, resolved as for
  `reviewed:`), written by the app's **Terminal** and `conductor open` beside the console
  they open, logs `session_opened` (`session_id`, `by`), so a later `report_changed`
  can name it.
- **review** — `review: all` or `review: <ref>` (one token; the ref resolves as for
  `reviewed:`, so `code/2` works) rewrites `review.md` whole: the unreviewed reports
  (or that one finished item's, reviewed or not) inlined, plus conductor's own past
  the planner's marker for `all` (layout: `inbox.md`, *Review bundle*), and logs `review`.
  It marks nothing reviewed. A queued or running item, an unknown or ambiguous ref, or
  a non-repo prefix is refused (`review: code/003_x has not finished; only a finished
  item's report can be reviewed`, `review: no finished item '009'`); a multi-word
  value fails the syntax check.
- **tell** — `tell: <item> <message>`: see `progress.md` (refused while the item is in setup).
  An item waiting on its own question gets it as that question's answer (`questions.md`).
- **pause** / **resume** — `pause: <repo>`, `resume: <repo>` (refused when already so):
  `night-shift.md`, *Pause*.
- **outline** — `outline: <item>`: a tell of the fixed outline request; see `progress.md`,
  *Outline*.
- **land** — `land: <item>` (a finished item, resolved as for `reviewed`): its kept branch
  lands on `main` as one commit, and the hold its red put on the queue is released; see
  `worktrees.md`, *Landing a kept branch*.
- **audit** — `audit: <item>` on a running item (resolved as for `tell`): conductor's own
  *Audit notes* text (`templates/audit.md`, the one `audit: true` gives at launch, with the
  item's notes path) sent as a tell marked `audit: true`; its run record gains `audit`, so
  its `finish` carries `audit: true` and the next cleanup reads its notes. Refused for an
  item not running, or one already keeping notes. A queued item takes `audit: true` in its
  front-matter instead (`templates.md`).
- **checkpoint** — `checkpoint: <label>` (any text, a ` # ` included, or none): see
  `inbox.md`.
- **session** — `session: <label>` (any text, or none): a checkpoint with that label,
  then `session.md`, then a `read` past it; see `inbox.md`, *Session start digest*.
- **run** — `run: <target>` or `run: <target> stop`: see `run-targets.md`. An unknown
  target is refused.
- **pick** — `pick: <repo>/<manifest path>`: see `picks.md`. The value is one word (a
  manifest path with a space, or ` #`, cannot be named) and its repo must be configured,
  or the file is rejected at parse time; a manifest that fails validation rejects it
  when applied, with the reason.
- **retire** — `retire: <picker name>`: deletes that picker's manifest and images; see
  `picks.md`, *Retiring*. An unknown or already retired name is refused.
- **open** — `open: <verb> <path or URL>`: one named desktop action on a checked path
  (`folder`, `reveal`, `file`, `text`, `url`); see `open.md`.
- **notify** — `notify: <text>`: a desktop toast; see `open.md`.
- **stop: daemon** — the daemon finishes the signals of that tick, logs `daemon_stop`
  (`reason: signal`), releases its lock and exits. Executors and stages keep running;
  the next start adopts them.
- **autopilot** — `autopilot: off|night|always` (`on` is `always`): logs `autopilot`
  (mode, was), which every later decision reads (`night-shift.md`, *Autopilot*).
- **ack** — `ack: invalid [#<event> …]` (none named: every live one) logs `ack`; those
  rejected signals leave the app's "N rejected" and the inbox (`inbox.md`), not Events.
  A non-live id rejects the file. The app's **Dismiss** sends `ack: invalid`.

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
