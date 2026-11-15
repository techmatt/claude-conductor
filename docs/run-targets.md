# Run targets

A project's long-running thing (a dev server, a game) is a **run target** in
`conductor.toml`:

```
[run.game]
repo = "game"                  # run in this repo's main checkout; or cwd = "<folder>" (relative to the control folder)
command = "npm run dev -- --port 5320 --strictPort"   # a string runs through the shell; or a list
url = "http://localhost:5320/" # opened in the default browser; its port in the project's block
ready = "http://localhost:5320/health"   # optional: what must answer first (default: url)
ready_seconds = 60             # optional: how long to wait for it
```

Every port a target names lies in the project's port block (`control-folder.md`, *Port
blocks*); `--strictPort` (Vite) makes a taken port an exit rather than a quiet move to the next.

The signal `run: <name>` (planner, app) or `conductor run-target <name>` (CLI):
- **Port held by something else:** see *Who holds the port*, below: `run_failed`, nothing
  starts or opens.
- **Already up** (its own process holds the port and the ready URL answers HTTP, any
  status): the URL opens at once, `run_open`.
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
of a closed local port can take up to its timeout, twice over for `localhost` (`::1`, then
`127.0.0.1`): 1s here; the app's header probe uses 0.3s and runs on a background thread
(`model.TargetProbe`), since on the UI thread it froze typing. The planner adds or changes targets with a
`conductor.toml` request.

## Who holds the port

(119) Before "already up" and before a start, and on each poll while starting, conductor
reads who listens on the ready URL's port (any local address; `procs.listeners`, psutil's
TCP table, ~3 ms). Only the target's own recorded process or a descendant counts as it
(`targets.taken`). Anyone else fails the request, `run_failed` with `error` "port 5180 is
held by <holder>; nothing was started or opened" (during a start: "…; nothing was opened",
conductor's own process left running). The holder is named by project and target or tool
when it is a conductor-started process in this or any project with a port block
("aicomplete's tool cards (pid 1234)"), else "node.exe (pid 1234), which conductor did not
start" (a server started by hand, or one whose record is gone: stop it to run the target).
Before this, Helium Hustle's `run: game` on 5180 found AI Complete's card viewer answering
there, counted itself up and opened the viewer. The app's run button shows such a target
red, `● taken`. A ready URL on another host is not checked.

A repo's main checkout has no installed dependencies: executors install them in their
worktrees, which are removed after the merge. So a target whose server needs them
installs them first, e.g. `command = "npm install && npm run dev -- --port 5320 --strictPort"`.
Name the port in `url` (or `ready`): conductor never hands a target's port to an
executor (`preambles.md`, *Port*).
