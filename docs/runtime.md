# The `.conductor/` folder

The daemon's own runtime state, inside the control folder and git-ignored with the rest
of the runtime paths (`control-folder.md`). Nothing in it is for the planner to read or
write.

- `daemon.json`: the daemon lock and heartbeat.
- `snapshot/`: the control-folder guard's snapshot (`control-folder.md`, *Control-folder guard*).
- `cancelled/`: cancelled items, kept.
- `reorder.json`, `signal.json`: crash journals for a reorder and for the signal file
  being applied (`signals.md`).
- `daemon.out.log`: the output of a daemon the app, a self-restart or `restart_all.py --force` started.
- `restart.out.log`: the app's **Restart all** run; `app.out.log`: the app `restart_all.py` or the app's own self-restart started (`restart.md`, `app.md`).
- `doc_sizes.json`: each digest's doc sizes, for the next one's growth (`inbox.md`).
- `progress/`, `mail/`: progress lines, and messages waiting for executors (`progress.md`).
- `touch/`: the control-folder paths each item's tool calls named, for tamper
  attribution (`control-folder.md`).
- `open.log`: `open:` launches (`open.md`).
- `restart.ready`: the self-restart handshake (`restart.md`).
- `memory.jsonl`: memory samples (`memory.md`).
- `load-items.json`: this daemon's items' pids, for other daemons' load lines (`memory.md`).
- `push/<target>.log`: the last push's output (`push.md`).
- `leftovers.json`: worktrees to retry removing (`worktrees.md`).
- `merge-attributes/<repo>.attributes`: the union-merge attributes a rebase lands `append_only` files with (`worktrees.md`).
- `landing/<repo>__NNN_name.json`: a `land:` still landing, resumed after a restart (`worktrees.md`).
- `run-<name>.log`, `run-<name>.json`: run targets (`run-targets.md`).
- `tool-<name>.log`, `tool-<name>.json`: project tools (`app.md`).
- `remote/`: remote workers' broker, bundles and jobs, in a project with `[remote]` (`remote-workers.md`).
- `now/<name>.line`: a *Now* line another process keeps fresh (the remote broker's; `inbox.md`).
- `setup/<repo>__NNN_name.run.json` or `.stage.json`, and their `.result.json`: a setup
  runner's spec and result, removed once setup ends (`executors.md`, *Setup*).
