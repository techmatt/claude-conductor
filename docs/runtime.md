# The `.conductor/` folder

The daemon's own runtime state, inside the control folder and git-ignored with the rest
of the runtime paths (`control-folder.md`). Nothing in it is for the planner to read or
write.

- `daemon.json`: the daemon lock and heartbeat.
- `snapshot/`: the control-folder guard's snapshot (`control-folder.md`, *Control-folder guard*).
- `cancelled/`: cancelled items, kept.
- `reorder.json`, `signal.json`: crash journals for a reorder and for the signal file
  being applied (`signals.md`).
- `daemon.out.log`: the output of a daemon the app started.
- `progress/`, `mail/`: progress lines, and messages waiting for executors (`progress.md`).
- `touch/`: the control-folder paths each item's tool calls named, for tamper
  attribution (`control-folder.md`).
- `open.log`: `open:` launches (`open.md`).
- `restart.ready`: the self-restart handshake (`restart.md`).
- `memory.jsonl`: memory samples (`memory.md`).
- `push/<target>.log`: the last push's output (`push.md`).
- `leftovers.json`: worktrees to retry removing (`worktrees.md`).
- `landing/<repo>__NNN_name.json`: a `land:` still landing, resumed after a restart (`worktrees.md`).
- `run-<name>.log`, `run-<name>.json`: run targets (`run-targets.md`).
- `setup/<repo>__NNN_name.run.json` or `.stage.json`, and their `.result.json`: a setup
  runner's spec and result, removed once setup ends (`executors.md`, *Setup*).
