# The app

`uv run conductor app [<control folder> …]` (or the `conductor-app` gui script, no
console) opens a PySide6 window with one tab per control folder. Folders named on the
command line are added; the list is remembered across launches (QSettings, org
`conductor`, app `conductor-app`, key `controls`) and edited with *Add project…* /
*Remove project*. A remembered folder that fails to load (drive not mounted,
`conductor.toml` broken) is reported in the status bar and stays remembered. Qt is
imported only by this command; the daemon and CLI never need it.
**One app per user.** The app listens on a per-user local server (`QLocalServer`
`conductor-app-<user>`: a named pipe on Windows, a socket file elsewhere). A later
`conductor app [<folder> …]` connects, sends `{"folders": [...]}` as one JSON line and
exits 0; the running app adds those folders (remembered), selects the last one named
and comes forward. So repeating a launch never adds a second tray icon, and no second
app rewrites the remembered list from its own stale copy. If the name can't be claimed
the app runs alone. Code: `app/single.py`.
Code: `src/conductor/app/` — `model.py`, `item_model.py` (an item's Conversation and trail),
`streamlog.py` (a stream-json log as text) and
`actions.py` have no Qt (they reuse `state.scan`, `state.decide`, `questions`, `config`). The widgets are `window.py`, one
`*_panel.py` per tab, `trail.py` (an item's detail and Conversation) and `widgets.py`
(what the panels share).

**What it reads and writes.** It opens `events.db` with a `file:…?mode=ro` URI, so it
can neither create nor write it; a missing `events.db` reads as an empty log and stays
missing. (It can leave an empty `events.db-wal` and `-shm`: SQLite's, harmless, ignored.)
The URI keeps a mapped drive as is and puts a UNC server in the path
(`file:////server/share/…`), which SQLite accepts. Every 1.5s it re-reads only events
with `id >` the last one seen, and rescans the control folder; a read that fails for a
moment (database busy) counts as no change, and only a log with fewer events than seen
(replaced) resets the Events table. New events reach the table before anything else
refreshes, and each panel refreshes on its own, so one bad file cannot freeze the rest.
On Windows its reads can briefly block the daemon's renames; the daemon retries those
(`util.retry_io`, up to 3s). It never moves a queue file. Its only writes are signal files, answer
files, its own settings, the model, starting the daemon, and opening a console on a finished session.

**Model: Opus / Fable** (135; toolbar, tray) sets the machine's model for new sessions
(`executors.md`, *Model*) in `~/.conductor/model.json`: no project's, so no signal.

**Header.** One short line: the state (`1 running · 1 staged`, `held after red: …`,
`2 open question(s)`, `idle`; the project is the tab's name), its daemon
(`.conductor/daemon.json`, pid + creation time: **up** since a time, amber **not
answering** when its heartbeat is stale, red **died** when its lock outlived it, or
**down**; the pid and heartbeat in its tooltip; see `memory.md`), a count of the app's
signals still waiting in `signals/`, and "N rejected": signals refused (`invalid`) since the
latest checkpoint and not acknowledged, with **Dismiss** (an `ack: invalid` signal; each
one's reason in its tooltip). A checkpoint clears the count too. A second line, the memory
line (`memory.line`, as in the inbox's *Now*), shows only when it needs Matt: amber under
pressure, red when critical (with the biggest item). At `ok` it is hidden and takes no
space; `conductor status` and the inbox's *Now* still show it always.
One daemon button toggles with its state: *Start daemon* spawns `<python> -m conductor run <folder>`
(console `python.exe` even under `pythonw.exe`; nothing flashes a window) detached through
a launcher that exits (`restart.md`, *Platform notes*), output appended unbuffered to
`.conductor/daemon.out.log`; *Stop daemon…* asks, then writes `stop: daemon`. Executors
and stages keep running either way. A daemon dead or silent 3 minutes: one toast, and
**Restart daemon** (139; `memory.md`, *Death made visible*).

Each run target gets a **Run <name>** button beside them, reading `● up`, `● starting`
or `● down` (green, amber, grey; probed each refresh on a background thread, so a button shows the previous refresh's answer); a click writes `run: <name>`,
which opens the URL (starting the target first when it is down). While a target is
up or starting, a **Stop <name>** button beside its Run button writes `run: <name>
stop` (no confirmation: a click is cheap to undo with Run).
**Tools** are the same idea for what Matt looks at rather than what he tests, in the
header's **Tools** menu (*Project tools*, below).
**Pause** / **Resume** writes `pause: <repo>` / `resume: <repo>` (`night-shift.md`, *Pause*):
a plain button for a one-repo project, a menu of `Pause <repo>` / `Resume <repo>` for
several; while any repo is paused an amber **PAUSED <repo> since <time>** sits beside it.
**Autopilot: Off / Night only / Always** is a menu writing `autopilot: <mode>`
(`night-shift.md`, *Autopilot*); beside it, muted, what autopilot starts next
(`nightshift.plan`). Labels that don't fit a narrow window are clipped, their tooltips whole.
The app has **no way to make a checkpoint** (112): a checkpoint comes only from a planner's `session:` / `checkpoint:` signal or the CLI
(`conductor session` / `checkpoint`), so a session's closeout can't be skipped. The Events
tab's *since checkpoint #N* still names the current one.

**Message boxes** (Tell…, Amend…, a question's answer): word-wrapped, never scrolling
sideways, several lines tall; **Enter** sends (as OK), **Shift+Enter** is a new line, and a
hint under the box says so (`widgets.SendBox`, `MessageDialog`).

**Code check.** Each `daemon_start` carries its code version; the app reads its own once.
When a live daemon's differs (after 60 the open app flagged 034 "unknown front-matter
fields" the daemon had staged), the header offers a **Restart all** button (68; since 71
also while items run: they are adopted, `restart.md`).

**Self-restart** (83; `app/stale.py`). Every 10s, off the GUI thread, the app reads the
checkout's committed version the daemon's way (`restart.md`, *Self-restart*). Once it
differs from the app's own, the checkout is settled and `conductor.app.window` imports in a
fresh interpreter (tried once per version), the app restarts at the first refresh with
nothing mid-action: no dialog or menu open, no unsent text in any answer or message box. It
stops serving forwarded launches, spawns `conductor app <its folders>` detached (output in
the first control folder's `.conductor/app.out.log`), and quits; the new window comes up
shown. Not after **Restart all** was clicked (the tool restarts it). Until then each project
shows a wrapped amber line under the header with the whole sentence: the commit, that this
app runs the old code, what it waits for (or why it can't: an unsettled checkout, an import
error) and that "Restart all" fixes it now. A daemon on other code than a current app gets
its own line. Before 83 the app never restarted itself, and a clipped header label read
"app and daemon run different code: `invalid`", cut at the backtick (aicomplete, twice on
2026-11-05).

**Panels**: Repos (with each item's Conversation), Questions, Events and Proposed, in
`docs/app-panels.md`.

**Project tools** are a project's local tools (AI Complete's card viewer), declared in
`conductor.toml` and set by a request:

```
[tools.cards]
label = "Card viewer"          # menu text (default: the name)
run = "npm run cards -- --port 5260 --strictPort"   # a string runs through the shell; or a list
cwd = "aicomplete/web"               # a repo's main checkout, plus an optional subfolder; left out: the only repo
url = "http://localhost:5260/cards.html"   # opened once it answers; its port in the project's block
```

Any other key is refused, and `cwd` must name a repo (required with several); `rename-repo`
rewrites it. A tool's port is never handed to an executor, and lies in the project's port
block (`control-folder.md`, *Port blocks*): a script with a fixed port is given its block's
one from `run` (`-- --port N`), not edited.

The header's **Tools ▾**, beside **Autopilot ▾**, lists them; a project with none shows no
menu. A running tool (from its record, with no probe) has a green dot, and **Stop** beside it
writes `tool: <name> stop` (`run_stop`, its process tree killed). Choosing one writes
`tool: <name>`, so the daemon must be running:
- **Its port held by something else** (not the tool's own recorded process tree): `run_failed`
  naming the holder, and nothing starts or opens (`run-targets.md`, *Who holds the port*).
- **Not answering:** `run` starts as a conductor-owned process at below-normal priority,
  everything it starts included, output in `.conductor/tool-<name>.log`, record in
  `tool-<name>.json`. When `url` answers (60s at most) it opens in the default browser.
- **Answering, or still starting:** nothing new starts. It opens, at once or when ready.
- **Fails** (the process exits, it can't start, or 60s pass, when it is left running):
  `run_failed` with `tool: true` and the log's last 20 lines. The header shows the error and
  the last line in red (tooltip: the tail) until the next start, open or stop; a toast and the
  inbox's Red section show it too. It flags no watch.

The events are `run_*` with `tool: true`. Stopping the daemon (`stop: daemon`, Ctrl+C, too
many failed ticks) stops its tools, `run_stop` with `stopped with the daemon`, as does
`restart_all.py --force`. A self-restart (on a code change, or the `restart: daemon` that
"restart all" sends) or a crash leaves a tool running and the next daemon owns it by its record. Like a run target, a tool
in a main checkout can hold a file a merge wants to replace (Windows).

Every action is a signal file `signals/app-<YYYYmmdd-HHMMSS>-<nnnnnn>.signal` (written as
`.tmp`, then renamed) starting `by: app`, so the daemon carries it out, logs it, and
nothing races a launch. With the daemon down, actions wait in `signals/`.

## Header and title

The icon, tab motion, the window title, *Waiting*, the tray and toast clicks: `app-status.md`.
