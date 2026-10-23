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
the app runs alone, as before. Code: `app/single.py`.
Code: `src/conductor/app/` — `model.py`, `streamlog.py` (a stream-json log as text) and
`actions.py` have no Qt (they reuse `state.scan`, `state.decide`, `questions`, `config`). The widgets are `window.py`, one
`*_panel.py` per tab, `trail.py` (an item's detail and Conversation) and `widgets.py`
(what the panels share).

**What it reads and writes.** It opens `events.db` with a `file:…?mode=ro` URI, so it
can neither create nor write it; a missing `events.db` reads as an empty log and stays
missing. (Opening a WAL database read-only while no daemon has it open leaves an empty
`events.db-wal` and an `events.db-shm` beside it — SQLite's, harmless, git-ignored.)
The URI keeps a mapped drive as is and puts a UNC server in the path
(`file:////server/share/…`), which SQLite accepts. Every 1.5s it re-reads only events
with `id >` the last one seen, and rescans the control folder; a read that fails for a
moment (database busy) counts as no change, and only a log with fewer events than seen
(replaced) resets the Events table. New events reach the table before anything else
refreshes, and each panel refreshes on its own, so one bad file cannot freeze the rest.
On Windows its reads can briefly block the daemon's renames; the daemon retries those
(`util.retry_io`, up to 3s). It never moves a queue file. Its only writes are signal files, answer
files, its own settings, starting the daemon, and opening a console on a finished session.

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
One daemon button toggles with its state: *Start daemon* spawns `<python> -m conductor run <folder>` (the
console `python.exe` even when the app runs under `pythonw.exe`; git calls pass
`CREATE_NO_WINDOW` too, so nothing flashes a console window)
detached through a launcher that exits (no live parent, no window, its own process group, out of the caller's job object when
allowed: `restart.md`, *Platform notes*), output appended unbuffered (`python -u`) to `.conductor/daemon.out.log`; *Stop daemon…* asks first,
then writes a `stop: daemon` signal. Executors and stages keep running either way.
Each run target gets a **Run <name>** button beside them, reading `● up`, `● starting`
or `● down` (green, amber, grey; probed each refresh); a click writes `run: <name>`,
which opens the URL (starting the target first when it is down). While a target is
up or starting, a **Stop <name>** button beside its Run button writes `run: <name>
stop` (no confirmation: a click is cheap to undo with Run).
**Pause** / **Resume** writes `pause: <repo>` / `resume: <repo>` (`night-shift.md`, *Pause*):
a plain button for a one-repo project, a menu of `Pause <repo>` / `Resume <repo>` for
several; while any repo is paused an amber **PAUSED <repo> since <time>** sits beside it.
**Autopilot: Off / Night only / Always** is a menu writing `autopilot: <mode>`
(`night-shift.md`, *Autopilot*); beside it, muted, what autopilot starts next
(`nightshift.plan`). **New checkpoint…** asks for a label (default `session start`) and writes
`checkpoint: <label>`; its tooltip names the current checkpoint. Labels that don't fit a
narrow window are clipped, their tooltips whole.

**Message boxes** (Tell…, Amend…, a question's answer): word-wrapped, never scrolling
sideways, several lines tall; **Enter** sends (as OK), **Shift+Enter** is a new line, and a
hint under the box says so (`widgets.SendBox`, `MessageDialog`).

**Panels**: Repos (with each item's Conversation), Questions, Events and Proposed, in
`docs/app-panels.md`.

Every action is a signal file `signals/app-<YYYYmmdd-HHMMSS>-<nnnnnn>.signal` (written as
`.tmp`, then renamed) starting `by: app`, so the daemon carries it out, logs it, and
nothing races a launch. With the daemon down, actions wait in `signals/`.

## Header and title

**Icon.** A tetrahedron's four nodes and six links around a gold hub, in navy:
`src/conductor/app/icon.svg` is the master; `conductor.ico` beside it (16, 20, 24, 32,
48, 256 px; rebuilt by `uv run python tools/build_icon.py`) is the window's and the
taskbar's. At 24 px and below the links and nodes are drawn bolder so the shape still
reads. The app sets its own AppUserModelID (`conductor.app`, `procs.set_app_id`), so the
taskbar shows this icon rather than Python's. Each project's tab carries the icon with
its hub red (needs Matt), gold (running) or slate (idle).

**Window title**: the most urgent thing across every open project, cheap enough to ride
the refresh:

| State | Title |
|---|---|
| Needs Matt: a blocking question, a red hold, a picker waiting, a daemon down with work | `● Conductor · 1 question` (`· red`, `· pick`, `· daemon down`; counts when more than one) |
| Items running (a stage still staging counts; a staged item waiting with its plan doesn't) | `◐ Conductor · 2 running`, the glyph turning ◐ ◓ ◑ ◒ once a second |
| Idle | `Conductor` |

Needs Matt ranks above running and holds still; unreviewed reports and non-blocking
questions never count (the planner handles those). The glyph's timer (`TURN_MS`) runs
only while items run. A project whose refresh fails part-way keeps its last whole
snapshot in the title. The header's state line above the tabs is unchanged.

**Tray.** The same three states on the icon's hub: red when something needs Matt,
pulsing gold (gold and dim on alternate seconds) while items run, plain gold when idle.
Its tooltip lists each project's state; clicking opens the window. Closing the window
hides it to the tray; *Quit* in the tray menu exits. Neither touches a daemon or an
executor. Without a system tray, closing the window quits.

**Toast click.** Not wired. Toasts are raised under Command Prompt's app id, so a plain
click is delivered to that app id, never to this app. `windows-toasts` can
make a toast *protocol*-activated (`Toast(launch_action="scheme:…")`), which Windows
honours for unpackaged apps; opening the app on an item would need a URI scheme
(e.g. `conductor-app:`) registered under `HKCU\Software\Classes` pointing at
`conductor-app`, plus a single-instance hand-off to a running app. Unverified and left
for later; the toast's tag (`<project>#<event id>`) names the event, and *Events* lists
ids in its first column.
