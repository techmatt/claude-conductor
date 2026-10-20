# Signals

The verbs: read, clear, approve, cancel, reorder, reviewed, tell, outline, checkpoint,
session, run, pick, stop.

Every change to the queue that is not a new item goes through `signals/`, so the daemon
carries it out between launches and logs it. The planner (or the app) writes a small
file (any name; `*.tmp` and `*.bad` are skipped) with `key: value` lines, applied in
order:

```
by: planner                  # optional: every event this file causes carries detail `by`
read: 42                     # inbox read through event #42 (`latest` = everything so far)
clear: sandbox               # release the sandbox queue after a red
approve: 007                 # proposed/007_x.md → queue/<its repo>/007_x.md
cancel: sandbox/004          # a queued item (or a proposed one: `cancel: 007`)
reorder: sandbox 006 004     # the listed items swap numbers; unlisted ones stay
reviewed: sandbox/003        # the planner has acted on that finished item's report
tell: sandbox/005 use sqlite # a message to running sandbox/005 (see progress.md)
outline: sandbox/005         # ask running sandbox/005 for an updated outline (a tell of fixed text)
checkpoint: mid-session      # a session boundary (see inbox.md)
session: start               # a checkpoint, then session.md, then the read mark moved (see inbox.md)
run: game                    # start run target `game` if needed, open its URL (`run: game stop`)
pick: code/picks/art_alts_009.json   # serve that picker manifest and open it in the browser
stop: daemon                 # the daemon exits cleanly, as on Ctrl+C
```

One file may carry several lines; the planner names its files `planner-<stamp>.signal`.
Items are named `NNN_name` or `NNN` (numerically: `7` is `007`). A ` # comment` after a
value is ignored.

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
  it is enqueued again. Renames go through temp names (`NNN_name.reorder~<new name>`,
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
  waits on the read mark. Several `reviewed:` lines may share a file.
- **tell** — `tell: <item> <message>`: see `progress.md`.
- **outline** — `outline: <item>`: a tell of the fixed outline request; see `progress.md`,
  *Outline*.
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
- **stop: daemon** — the daemon finishes the signals of that tick, logs `daemon_stop`
  (`reason: signal`), releases its lock and exits. Executors and stages keep running;
  the next start adopts them.

Conductor processes a file about a second after it stops changing and deletes it. The
whole file is syntax-checked first: an unknown key or malformed line rejects it with
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
