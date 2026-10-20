# Run targets

A project's long-running thing (a dev server, a game) is a **run target** in
`conductor.toml`:

```
[run.game]
repo = "game"                  # run in this repo's main checkout; or cwd = "<folder>" (relative to the control folder)
command = "npm run dev"        # a string runs through the shell; or a list: ["python", "-m", "http.server", "8765"]
url = "http://localhost:5173/" # opened in the default browser
ready = "http://localhost:5173/health"   # optional: what must answer first (default: url)
ready_seconds = 60             # optional: how long to wait for it
```

The signal `run: <name>` (planner, app) or `conductor run-target <name>` (CLI):
- **Already up** (the ready URL answers HTTP, any status): the URL opens at once,
  `run_open`.
- **Down:** the command starts detached (as the app starts the daemon: no window, its
  own process group, out of the caller's job; a daemon stop leaves it running), output
  appended to `.conductor/run-<name>.log`, pid and creation time in
  `.conductor/run-<name>.json`; `run_start`. When the ready URL answers, `run_ready`
  (waited_seconds) and the URL opens, `run_open`.
- **Starting already:** its recorded process is alive but not answering yet, so nothing
  new starts (`run_start` with `reused`). One process per target: a dead recorded
  process is simply started again.
- **Never comes up:** the process exits first, or `ready_seconds` pass (the process is
  then left running): `run_failed`, a red kind, with `error` and the log's last 20 lines
  (`log_tail`). It leads the inbox's Red section and toasts, but holds no queue.

`run: <name> stop` (`conductor run-target <name> --stop`, the app's **Stop <name>**)
kills the recorded process tree; `run_stop`. The daemon never blocks on a start: it checks pending targets every
tick. A daemon restart forgets a start it was waiting on (run it again: it opens at
once if up). The CLI waits in the foreground and prints the events. On Windows a probe
of a closed local port can take up to its timeout (1s; the app's header probe uses
0.3s per target per refresh). The planner adds or changes targets with a
`conductor.toml` request.

A repo's main checkout has no installed dependencies: executors install them in their
worktrees, which are removed after the merge. So a target whose server needs them
installs them first, e.g. `command = "npm install && npm run dev -- --port 5173"`.
Name the port in `url` (or `ready`): conductor never hands a target's port to an
executor (`executors.md`, *Port*).
