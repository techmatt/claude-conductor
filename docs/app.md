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
Code: `src/conductor/app/` — `model.py` and `actions.py` have no Qt (they reuse
`state.scan`, `state.decide`, `questions`, `config`). The widgets are `window.py`, one
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

**Header.** The project's state, its daemon (`.conductor/daemon.json`, pid + creation
time: **running**, amber **not answering** when its heartbeat is stale, red **died**
when its lock outlived it, or **not running**; see `memory.md`), and a count of the app's
signals still waiting in `signals/` (or rejected as `.bad`). A second line is the memory
line (`memory.line`, as in the inbox's *Now*), amber under pressure, red when critical.
*Start daemon* spawns `<python> -m conductor run <folder>` (the
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
**New checkpoint…** asks for a label (default `session start`) and writes
`checkpoint: <label>`; its tooltip names the current checkpoint.

**Panels**: Repos (with each item's Conversation), Questions, Events and Proposed, in
`docs/app-panels.md`.

Every action is a signal file `signals/app-<YYYYmmdd-HHMMSS>-<nnnnnn>.signal` (written as
`.tmp`, then renamed) starting `by: app`, so the daemon carries it out, logs it, and
nothing races a launch. With the daemon down, actions wait in `signals/`.

**Tray.** A coloured disc drawn at runtime shows the most urgent state across all
projects: red (a repo held after a red, or a project's daemon down while it has items
queued or running) > asking (an open question) > running (an item running or staged)
> idle (grey). Its tooltip lists each project's state; clicking opens the window.
Closing the window hides it to the tray; *Quit* in the tray menu exits. Neither touches
a daemon or an executor. Without a system tray, closing the window quits.

**Toast click.** Not wired. Toasts are raised under Command Prompt's app id, so a plain
click is delivered to that app id, never to this app. `windows-toasts` can
make a toast *protocol*-activated (`Toast(launch_action="scheme:…")`), which Windows
honours for unpackaged apps; opening the app on an item would need a URI scheme
(e.g. `conductor-app:`) registered under `HKCU\Software\Classes` pointing at
`conductor-app`, plus a single-instance hand-off to a running app. Unverified and left
for later; the toast's tag (`<project>#<event id>`) names the event, and *Events* lists
ids in its first column.
