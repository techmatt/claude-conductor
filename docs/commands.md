# Commands

Every `conductor` subcommand (`src/conductor/cli.py`). `farm-seeds` and the remote
worker's own command lines are in `remote-workers.md`.

```
uv sync
uv run conductor run    <control folder>   # the daemon, foreground; Ctrl+C stops it
uv run conductor run --detach <control folder>   # detached, as the app's Start daemon; safe from Claude Code
uv run conductor status [<control folder>]
uv run conductor inbox  [<control folder>]
uv run conductor events [<control folder>] [--since X] [--item Y] [--repo R] [--kind K] [--paths]
uv run conductor questions [<control folder>] [--all]      # open asks (--all: resolved too)
uv run conductor audits [<control folder>] [--all]         # unused audit notes per repo (--all: used and claimed too)
uv run conductor answer <id> <text…> [--control <folder>]  # writes the answer file
uv run conductor tell <item> <text…> [--control <folder>]  # a message to a running executor
uv run conductor outline <item> [--control <folder>]       # ask a running executor for an updated outline
uv run conductor amend <item> <text…|file> [--control <folder>]  # add to an item's instructions (progress.md)
uv run conductor checkpoint [<label…>] [--control <folder>]   # a new checkpoint (inbox.md)
uv run conductor session [<label…>] [--control <folder>]      # a checkpoint plus the session.md digest (inbox.md)
uv run conductor open <item> [--control <folder>]          # a finished item's session, in a new console
uv run conductor run-target <name> [--stop] [--control <folder>]   # start a [run.<name>] target, open its URL
uv run conductor pick <repo>/<manifest path> [--control <folder>]  # open a picker in the browser (picks.md)
uv run conductor open-path <folder|reveal|file|text|url> <target…> [--control <folder>]  # the open: verbs (open.md)
uv run conductor init <control folder> --project NAME --repo NAME=PATH [--repo …]
uv run conductor rename-repo <old> <new> [--path P] [--control <folder>]   # daemon stopped (rename-repo.md)
uv run conductor app [<control folder> …]                  # the desktop app (app.md)
```

The control folder argument defaults to `$CONDUCTOR_CONTROL`, then the current folder.
`conductor app` with no folder adds `$CONDUCTOR_CONTROL` if set, else the current folder
only when it holds a `conductor.toml`; it always opens its remembered projects too.
One daemon per control folder: a second `run` refuses while the first is alive. `run
--after PID` (wait up to 60s for that process to exit first) is how a daemon restarting
itself starts its successor (`restart.md`).

`--since` takes an event id (`#12` or `12`, exclusive), a relative age (`30m`, `2h`,
`1d`), a clock time today (`5:25pm`), an ISO time, `checkpoint` (from the latest
checkpoint's own event on) or `checkpoint N` (from checkpoint #N on). `--item` takes
`sandbox/001_x`, `001_x` or `001`.

Stopping the daemon does **not** stop executors (the next start adopts them); a **tree**
kill does, so start it from the app or a terminal, never as a Claude Code background
command (`restart.md`).
